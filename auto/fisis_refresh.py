"""FISIS 분기 신규자료 자동 갱신 (서버 크론용) — 싼 probe → 감지 시에만 전체 갱신.

FISIS 발표 스케줄(매년 동일): 분기말 자료 → **분기말+3개월의 25~31일** 중 발표(매일 17:00경).
  202512 → 2026.03.26~31 · 202603 → 2026.06.25~30 · 202606 → 2026.09.25~30 · 202609 → 2026.12.26~31

동작:
  1) 오늘 기준 '이미 발표됐어야 할' 최신 분기말 = target 계산(발표월 25일 이후면 해당분기 대상).
  2) fisis_stats에 target 이미 있으면 → 즉시 종료(0콜).
  3) probe(최대 3콜): API에 target 있나? 없으면 종료('아직 미공개') → 내일 다시.
  4) 있으면 → 전 (회사×통계표) 쌍을 최근구간으로 재조회해 upsert.

평소 0콜 · 타깃 대기중 1~3콜/일 · 감지된 날만 ~17,000콜(1회). 발표가 늦어도 자동으로 따라잡음.

사용:
  python fisis_refresh.py            # 크론용(자동 target)
  python fisis_refresh.py --probe    # probe만(갱신 안 함) — 점검용
  python fisis_refresh.py --target 202606 --force   # 특정 분기 강제 갱신
"""
from __future__ import annotations
import sys, time, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import requests
import config
import supabase_client as sb
import fisis_collect as fc

KST = timezone(timedelta(hours=9))
_H = {"apikey": config.SUPABASE_SERVICE_ROLE_KEY,
      "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}"}
URL = config.SUPABASE_URL + "/rest/v1/fisis_stats"
# 쌍(회사×통계표) 목록을 뽑을 기준 기간 — Q는 최근분기, H/Y는 직전 12월에 존재
PAIR_PERIODS = None  # target 기준으로 계산


def log(m):
    print(f"[{datetime.now(KST):%m-%d %H:%M:%S}] {m}", flush=True)


def target_period(today=None):
    """오늘 기준 '발표가 끝났어야 할' 최신 분기말 YYYYMM.

    FISIS는 분기말+3개월의 **25~31일에 걸쳐 나눠서** 올린다(매일 17:00경). 첫날 감지하자마자
    갱신하면 그날 올라온 업종만 받고, 다음날엔 have_period>0이라 '이미 보유'로 건너뛰어
    나머지 업종이 다음 분기까지 누락된다. → **발표창이 닫힌 뒤(발표월 다음달 1일~)** 실행한다.
    (지연 1주는 분기 데이터에 무의미. 발표가 늦어도 probe가 매일 확인하므로 자동 추적.)
    """
    d = today or datetime.now(KST).date()
    for back in range(0, 5):                      # 최근 분기말부터 역순
        m = ((d.month - 1) // 3) * 3 + 1          # 이번 분기 시작월
        y = d.year
        m -= 3 * back
        while m <= 0:
            m += 12
            y -= 1
        qend_m = m + 2                            # 분기말 월(3/6/9/12)
        qend_y = y
        if qend_m > 12:
            qend_m -= 12
            qend_y += 1
        # 발표창 = 분기말+3개월의 25~31일 → 그 **다음달 1일**부터 실행
        pm, py = qend_m + 4, qend_y
        while pm > 12:
            pm -= 12
            py += 1
        if d >= datetime(py, pm, 1, tzinfo=KST).date():
            return f"{qend_y}{qend_m:02d}"
    return None


def _year_ago(pm):
    return f"{int(pm[:4]) - 1}{pm[4:]}"


def is_complete(target, ratio=0.95):
    """target 분기가 '다 들어왔나' — 작년 같은 분기 행수의 ratio 이상이면 완결로 본다.

    직전 분기와 비교하면 안 된다(Q2·Q4엔 반기표, Q4엔 연간표가 더 붙어 기준이 달라짐).
    부분발표·중단된 갱신을 '완료'로 오인하지 않게 하는 자가복구 장치.
    """
    have = have_period(target)
    base = have_period(_year_ago(target))
    if base == 0:                                  # 비교 기준 없음 → 보유분 있으면 완결로 간주
        return have > 0, have, base
    return have >= base * ratio, have, base


def have_period(pm):
    r = requests.get(URL, headers={**_H, "Prefer": "count=exact", "Range": "0-0"},
                     params={"select": "finance_cd", "period_ym": f"eq.{pm}"}, timeout=40)
    return int(r.headers.get("content-range", "0/0").split("/")[-1])


PAIRS_URL = config.SUPABASE_URL + "/rest/v1/fisis_pairs"


def fetch_pairs(target=None):
    """DB에 존재하는 (회사×통계표) 쌍 **전체** — fisis_pairs 뷰가 정본.

    뷰를 쓰는 이유: 기간 샘플링으로 쌍을 뽑으면 최신치가 오래된 표(예: 연간표·휴면 표)가
    목록에서 빠져 **영구 미갱신**이 된다. 뷰는 fisis_stats에 한 행이라도 있으면 반드시 포함.
    """
    sel = "finance_cd,list_no,corp_code,finance_nm,sector,div,list_nm,last_period"
    pairs, seen, cursor = [], set(), None
    while True:
        p = {"select": sel, "limit": 1000, "order": "finance_cd.asc,list_no.asc"}
        if cursor:
            p["finance_cd"] = f"gte.{cursor}"     # keyset — OFFSET 금지(집계뷰 재계산→57014 타임아웃)
        rows = None
        for attempt in range(4):                  # 일시적 500/타임아웃 재시도
            r = requests.get(PAIRS_URL, headers=_H, params=p, timeout=90)
            if r.status_code == 200:
                rows = r.json()
                break
            time.sleep(2 * (attempt + 1))
        if rows is None:
            log(f"  ! fisis_pairs 뷰 조회 실패 — auto/fisis_pairs_view.sql 실행 여부 확인. "
                f"갱신 중단(부분갱신 방지).")
            return []
        if not rows:
            break
        fresh = 0
        for x in rows:
            k = (x["finance_cd"], x["list_no"])
            if k not in seen:
                seen.add(k)
                pairs.append(x)
                fresh += 1
        last = rows[-1]["finance_cd"]
        if len(rows) < 1000:
            break
        if last == cursor and fresh == 0:         # 진전 없음 = 종료(무한루프 방지)
            break
        cursor = last
    return pairs


def _corp_code_fixer(items):
    """corp_code 복구기 → fn(item) -> corp_code.

    items 의 corp_code 는 fisis_pairs 뷰의 `max(corp_code) group by finance_cd, list_no` 다.
    **쌍 단위 집계**라 어떤 (회사,표) 한 쌍이 NULL 로 적재되면 뷰도 NULL 을 돌려주고, 그걸 그대로
    다시 upsert 해 **NULL 이 자기 자신을 영속시킨다** — 갱신을 아무리 돌려도 영영 안 고쳐진다.
    실제 사고: 저축은행 6사 SE010(수익성표) 이 corp_code NULL 로 굳어 Disclo 지표 탭의
    ROE·ROA·NIM 이 통째로 비었다. 같은 회사의 다른 33개 표에는 corp_code 가 멀쩡히 있었는데도.

    복구 순서: ① 같은 배치 안 같은 회사의 비-NULL 값  ② resolve_corp(회사명)
    ⚠ 둘 다 실패하면 NULL 유지 — 외국계 '○○ 서울지점'은 DART 미등록이라 NULL 이 **정답**이다.
    """
    by_co = {}
    for it in items:
        cc = it.get("corp_code")
        if cc:
            by_co.setdefault(it["finance_cd"], cc)
    cache = {}

    def fix(it):
        cc = it.get("corp_code")
        if cc:
            return cc
        fcd = it["finance_cd"]
        cc = by_co.get(fcd)
        if cc:
            return cc
        if fcd not in cache:
            cache[fcd] = fc.resolve_corp(it.get("finance_nm") or "")
            if cache[fcd]:
                log(f"  · corp_code 복구: {it.get('finance_nm')} → {cache[fcd]}")
                by_co[fcd] = cache[fcd]
        return cache[fcd]
    return fix


def _upsert_grid(items, target=None, full=True):
    """(회사,표) 조합들을 수집·upsert. full=True면 전체이력(CHUNKS), 아니면 최근 1년."""
    rows = 0
    fix_cc = _corp_code_fixer(items)
    for it in items:
        if fc.keys_exhausted():
            log("  ! 키 소진 — 중단")
            break
        fcd, ln = it["finance_cd"], it["list_no"]
        agg = {}
        if full:
            for s, e in fc.CHUNKS:
                agg.update(fc.parse(fc.fisis("statisticsInfoSearch", financeCd=fcd, listNo=ln,
                                             term="Q", startBaseMm=s, endBaseMm=e)))
            if not agg:
                agg.update(fc.parse(fc.fisis("statisticsInfoSearch", financeCd=fcd, listNo=ln,
                                             term="Y", startBaseMm="199912", endBaseMm="202612")))
            if not agg:
                for s, e in fc.HCHUNKS:
                    agg.update(fc.parse(fc.fisis("statisticsInfoSearch", financeCd=fcd, listNo=ln,
                                                 term="H", startBaseMm=s, endBaseMm=e)))
        cc = fix_cc(it)                       # 뷰의 NULL 을 그대로 되쓰지 않는다(_corp_code_fixer 참고)
        buf = [{"finance_cd": fcd, "list_no": ln, "period_ym": p, "corp_code": cc,
                "finance_nm": it.get("finance_nm"), "sector": it.get("sector"), "div": it.get("div"),
                "list_nm": it.get("list_nm"), "data": accs} for p, accs in agg.items()]
        if buf:
            sb.upsert("fisis_stats", buf, "finance_cd,list_no,period_ym")
            rows += len(buf)
        time.sleep(0.03)
    return rows


def scan_new(pairs, aum_min_eok=500, scan_tables=False, coverage_min=0.8):
    """DB에 담고 있는 업종에 한해 **신규 금융사** 탐지 (+옵션: 신규 통계표).

    업종 목록을 하드코딩하지 않고 fisis_pairs의 distinct sector에서 도출 →
    '지금 담고 있는 업종'과 항상 일치(업종이 늘면 자동 반영).
    비용: 업종당 companySearch 1콜 (+ scan_tables면 statisticsListSearch 1콜).

    ⚠️ scan_tables 기본 OFF — FISIS 표 목록엔 있으나 **회사별 데이터가 없는 표**(업권 집계표 등)가
    수십 종이라, 켜두면 매분기 수천 콜을 빈손 재시도한다(2026-07 실측: bank SA048~052,
    lifeins SH116/SH168, card SC119/121, advisory SX* 등 전부 데이터 0 확인). 필요 시 --scan-tables.
    """
    have_co = {p["finance_cd"] for p in pairs}
    tabs_by_sec, meta_by_sec, co_by_sec = {}, {}, {}
    for p in pairs:
        s = p.get("sector")
        if not s:
            continue
        tabs_by_sec.setdefault(s, set()).add(p["list_no"])
        meta_by_sec.setdefault(s, p.get("div"))
        co_by_sec.setdefault(s, set()).add(p["finance_cd"])
    sectors = sorted(tabs_by_sec)
    log(f"담고 있는 업종 {len(sectors)}개: {', '.join(sectors)}")

    new_co, new_tab = [], []
    for sec in sectors:
        div = fc.SEC_DIV.get(sec) or meta_by_sec.get(sec)
        if not div:
            continue
        alive = [c for c in (fc.fisis("companySearch", partDiv=div).get("list") or [])
                 if not fc.is_dead(c.get("finance_nm"))]
        cov = len(co_by_sec[sec]) / len(alive) if alive else 0

        # 커버리지 게이팅 — '신입 편입'은 **전량 백필한 업종**에서만 의미가 있다.
        # advisory(1/459)·newtech(10/133)처럼 애초에 전량 백필 안 한 업종은 fin_stats 잔재일 뿐이라,
        # 미보유사를 '신규'로 보면 수백 사를 통째 끌어와 범위가 폭발한다(2026-07 실측 584사).
        # assetmgmt는 커버리지 13%지만 **의도적 총자산 필터**라 예외로 스캔(아래 AUM 컷 적용).
        if sec != "assetmgmt" and cov < coverage_min:
            log(f"  · {sec} 커버리지 {cov:.0%}({len(co_by_sec[sec])}/{len(alive)}) "
                f"— 전량 백필한 업종이 아니라 신규 편입 제외(범위 유지)")
            continue

        # 1) 신규 회사
        for c in alive:
            fcd, nm = c.get("finance_cd"), c.get("finance_nm")
            if not fcd or fcd in have_co:
                continue
            if sec == "assetmgmt":                    # 스코프 일관성: 총자산 500억↑만
                p = fc.parse(fc.fisis("statisticsInfoSearch", financeCd=fcd, listNo="SG202",
                                      term="Q", startBaseMm="202012", endBaseMm="202612"))
                v = 0.0
                if p:
                    e = p[max(p)].get("A")
                    if e and e.get("a"):
                        try:
                            v = float(str(e["a"]).replace(",", ""))
                        except Exception:
                            v = 0.0
                if v < aum_min_eok * 1e8:
                    continue
            new_co.append({"finance_cd": fcd, "finance_nm": nm, "sector": sec, "div": div,
                           "corp_code": fc.resolve_corp(nm)})
        # 2) 신규 통계표 (opt-in — 위 docstring 참조: 데이터 없는 표가 다수라 기본 OFF)
        if scan_tables:
            for t in fc.fisis("statisticsListSearch", lrgDiv=div).get("list") or []:
                ln = t.get("list_no")
                if ln and ln not in tabs_by_sec[sec]:
                    new_tab.append({"sector": sec, "div": div, "list_no": ln, "list_nm": t.get("list_nm")})
    return new_co, new_tab, tabs_by_sec


def backfill_new(new_co, new_tab, tabs_by_sec, pairs):
    """신규 회사 = 그 업종 전 통계표 · 신규 표 = 그 업종 전 회사 → 전체이력 백필."""
    rows = 0
    if new_co:
        log(f"🆕 신규 금융사 {len(new_co)}곳: " +
            ", ".join(f"{c['finance_nm']}({c['sector']})" for c in new_co[:8]) +
            (" …" if len(new_co) > 8 else ""))
        items = []
        for c in new_co:
            nms = {p["list_no"]: p.get("list_nm") for p in pairs if p.get("sector") == c["sector"]}
            for ln in sorted(tabs_by_sec[c["sector"]]):
                items.append({**c, "list_no": ln, "list_nm": nms.get(ln)})
        log(f"   → {len(items):,} 쌍 백필(전체이력)")
        rows += _upsert_grid(items)
    if new_tab:
        log(f"🆕 신규 통계표 {len(new_tab)}종: " +
            ", ".join(f"{t['sector']}/{t['list_no']}" for t in new_tab[:8]))
        # 회사 대표 pair 선정 — corp_code 가 **있는** pair 를 우선한다.
        # 단순 덮어쓰기(마지막 pair 가 이김)면 그 회사의 마지막 표가 corp_code NULL 인 순간
        # 신규 표 전체가 NULL 로 적재된다(= 저축은행 SE010 사고의 씨앗).
        co_by_sec = {}
        for p in pairs:
            d = co_by_sec.setdefault(p.get("sector"), {})
            cur = d.get(p["finance_cd"])
            if cur is None or (not cur.get("corp_code") and p.get("corp_code")):
                d[p["finance_cd"]] = p
        items = []
        for t in new_tab:
            for fcd, p in (co_by_sec.get(t["sector"]) or {}).items():
                items.append({"finance_cd": fcd, "list_no": t["list_no"], "list_nm": t["list_nm"],
                              "sector": t["sector"], "div": t["div"],
                              "corp_code": p.get("corp_code"), "finance_nm": p.get("finance_nm")})
        log(f"   → {len(items):,} 쌍 백필(전체이력)")
        rows += _upsert_grid(items)
    if not new_co and not new_tab:
        log("신규 회사·통계표 없음")
    return rows


def probe(pairs, target):
    """대표 쌍 몇 개로 target 공개 여부 확인 (최대 3콜)."""
    for x in pairs[:3]:
        got = fc.parse(fc.fisis("statisticsInfoSearch", financeCd=x["finance_cd"],
                                listNo=x["list_no"], term="Q",
                                startBaseMm=target, endBaseMm=target))
        if target in got:
            return True
    return False


def refresh(pairs, target):
    """각 쌍을 최근 1년 구간으로 재조회(Q→Y→H 폴백) → upsert."""
    y, m = int(target[:4]), int(target[4:])
    s, e = f"{y-1}{m:02d}", target
    rows = done = 0
    for i, x in enumerate(pairs, 1):
        if fc.keys_exhausted():
            log(f"  ! 키 소진 — {i-1}/{len(pairs)} 쌍에서 중단")
            break
        fcd, ln = x["finance_cd"], x["list_no"]
        agg = fc.parse(fc.fisis("statisticsInfoSearch", financeCd=fcd, listNo=ln,
                                term="Q", startBaseMm=s, endBaseMm=e))
        if not agg:
            agg = fc.parse(fc.fisis("statisticsInfoSearch", financeCd=fcd, listNo=ln,
                                    term="Y", startBaseMm=s, endBaseMm=e))
        if not agg:
            agg = fc.parse(fc.fisis("statisticsInfoSearch", financeCd=fcd, listNo=ln,
                                    term="H", startBaseMm=s, endBaseMm=e))
        buf = [{"finance_cd": fcd, "list_no": ln, "period_ym": p, "corp_code": x.get("corp_code"),
                "finance_nm": x.get("finance_nm"), "sector": x.get("sector"), "div": x.get("div"),
                "list_nm": x.get("list_nm"), "data": accs} for p, accs in agg.items()]
        if buf:
            sb.upsert("fisis_stats", buf, "finance_cd,list_no,period_ym")
            rows += len(buf)
        done += 1
        if done % 1000 == 0:
            log(f"  … {done}/{len(pairs)} 쌍 · {rows:,}행 · 콜 {fc._K['total']}")
        time.sleep(0.03)
    return done, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="probe만 하고 종료")
    ap.add_argument("--target", help="분기말 YYYYMM 강제 지정")
    ap.add_argument("--force", action="store_true", help="이미 보유해도 갱신 강행")
    ap.add_argument("--no-scan", action="store_true", help="신규 회사 탐지 생략")
    ap.add_argument("--scan-tables", action="store_true",
                    help="신규 통계표도 탐지(기본 OFF — 데이터 없는 표 다수라 헛콜)")
    ap.add_argument("--scan-only", action="store_true", help="신규 탐지만(기존 갱신 생략) — 점검용")
    ap.add_argument("--aum-min-eok", type=int, default=500, help="자산운용 편입 최소 총자산(억)")
    ap.add_argument("--coverage-min", type=float, default=0.8,
                    help="신규 편입을 허용할 업종 최소 커버리지(기본 0.8) — 미만은 전량백필 안 한 업종으로 보고 제외")
    ap.add_argument("--per-key-calls", type=int, default=9990)
    a = ap.parse_args()
    fc._K["cap"] = a.per_key_calls

    # 점검용: 신규 탐지만 (target 게이팅 무시)
    if a.scan_only:
        pairs = fetch_pairs()
        log(f"DB 쌍(회사×통계표): {len(pairs):,}")
        if not pairs:
            return
        new_co, new_tab, _ = scan_new(pairs, a.aum_min_eok, a.scan_tables, a.coverage_min)
        log(f"--scan-only: 신규 회사 {len(new_co)} · 신규 표 {len(new_tab)} (백필 생략) · 콜 {fc._K['total']}")
        for c in new_co:
            log(f"   신규사: {c['finance_nm']} ({c['sector']}/{c['finance_cd']}) corp={c['corp_code'] or '-'}")
        for t in new_tab:
            log(f"   신규표: {t['sector']}/{t['list_no']} {t['list_nm']}")
        return

    target = a.target or target_period()
    if not target:
        log("target 계산 불가 — 종료")
        return
    done, have, base = is_complete(target)
    log(f"target={target} · 보유 {have:,}행 (작년 동분기 {base:,}) · 키 {len(fc._K['keys'])}개")

    if done and not a.force:
        log(f"완결 → 종료 (0콜)")
        return
    if have > 0:
        log(f"부분 보유({have:,}/{base:,}, {have/base*100 if base else 0:.0f}%) → 미완으로 보고 갱신 진행")

    pairs = fetch_pairs(target)
    log(f"갱신 대상 쌍(회사×통계표): {len(pairs):,}")
    if not pairs:
        log("쌍 없음 — 종료")
        return

    if not probe(pairs, target):
        log(f"{target} 아직 API 미공개 → 종료 (probe {fc._K['total']}콜) · 내일 재시도")
        return
    log(f"✅ {target} 공개 감지 → 갱신 시작")
    if a.probe:
        log("--probe 모드라 갱신 생략")
        return

    # 1) 기존 쌍 갱신 (DB에 있는 것은 전부)
    done, rows = refresh(pairs, target)
    log(f"기존 쌍 갱신: {done:,}/{len(pairs):,} · {rows:,}행")

    # 2) 신규 금융사·통계표 편입 (담고 있는 업종 한정)
    nrows = 0
    if not a.no_scan:
        new_co, new_tab, tabs_by_sec = scan_new(pairs, a.aum_min_eok, a.scan_tables, a.coverage_min)
        nrows = backfill_new(new_co, new_tab, tabs_by_sec, pairs)

    log(f"=== 갱신 완료 · 기존 {rows:,}행 + 신규 {nrows:,}행 · 총 콜 {fc._K['total']} ===")


if __name__ == "__main__":
    main()
