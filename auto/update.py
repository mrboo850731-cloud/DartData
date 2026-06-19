"""DartData 상시 워커 증분 업데이트 → Supabase 직접 upsert (로컬 JSON 안 거침).

핵심 아이디어(중복 0, 항상 최신): 이벤트는 **period=연도** 키를 써서, 최근 활동이 있는
회사를 '그 회사의 현재연도 전체'로 다시 긁어 그 연도 묶음을 통째로 덮어쓴다. 몇 번을
돌려도 같은 키 → upsert 덮어쓰기라 누적 중복이 없다.

모드:
  python update.py events [--lookback 3]   # 15분 크론: 최근 N일 활동 회사의 현재연도 이벤트 갱신
  python update.py financials              # 일 1회 크론: 현재연도 재무 다중회사 갱신
  python update.py companies               # (선택) 현재연도 제출자 기업개황 갱신

events 의 period 정규화: 기존 백필 events(period="YYYYMMDD-YYYYMMDD")는 배포 시 SQL로
period→연도("YYYY")로 한 번 정규화해야 키가 일치한다(README/배포가이드 참조).
"""
from __future__ import annotations
import sys
import time
import argparse
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import requests
import config
import dart_api
import registry
import supabase_client as sb
import enumerate_filings as enm

KST = timezone(timedelta(hours=9))


def log(m):
    print(f"[{datetime.now(KST):%m-%d %H:%M:%S}] {m}", flush=True)


def _batch_upsert(table, rows, on_conflict, n=200):
    for i in range(0, len(rows), n):
        sb.upsert(table, rows[i:i + n], on_conflict)


def _changed_only(table, rows):
    """이미 저장된 것과 data가 동일한 행은 제외 — 불필요한 upsert(=updated_at 갱신=Disclo 재빌드 churn) 방지.
    resweep(재훑기)는 대부분 '변화 없는 재확인'이라, 실제로 바뀐(또는 새) 건만 남긴다.
    조회 실패 시 전부 통과(누락보다 중복 기록이 안전)."""
    import json as _json
    if not rows:
        return rows
    corps = sorted({r["corp_code"] for r in rows})
    existing = {}
    for i in range(0, len(corps), 100):
        inlist = ",".join(corps[i:i + 100])
        try:
            got = sb.get_all(f"{table}?select=endpoint,corp_code,period,data"
                             f"&corp_code=in.({inlist})&order=corp_code,endpoint,period")
        except Exception:
            return rows
        for g in got:
            existing[(g["endpoint"], g["corp_code"], str(g["period"]))] = g.get("data")

    def norm(d):
        return _json.dumps(d, sort_keys=True, ensure_ascii=False)

    out = []
    for r in rows:
        k = (r["endpoint"], r["corp_code"], str(r["period"]))
        if k not in existing or norm(existing[k]) != norm(r.get("data")):
            out.append(r)
    return out


def _changed_fin(rows):
    """financials 전용 변경필터 — 저장본과 내용(acct·idx·currency·rcept_no)이 같으면 제외.
    재무 워커가 매일 올해 제출자 전체를 데이터 변화와 무관하게 재upsert하면 updated_at이 들썩여
    Disclo 증분이 매번 full로 폴백한다(2026-06-19 churn 93% 원인). 한 호출=동일 (year,reprt).
    저장본도 동일 코드가 만든 값이라 JSON 직렬화가 일치 → 안전. 조회 실패 시 전부 통과(누락 방지)."""
    import json as _json
    if not rows:
        return rows
    yr, reprt = rows[0]["year"], rows[0]["reprt"]
    existing = {}
    corps = sorted({r["corp_code"] for r in rows})
    for i in range(0, len(corps), 100):
        inlist = ",".join(corps[i:i + 100])
        try:
            got = sb.get_all(f"financials?select=corp_code,acct,idx,currency,rcept_no"
                             f"&year=eq.{yr}&reprt=eq.{reprt}&corp_code=in.({inlist})&order=corp_code")
        except Exception:
            return rows
        for g in got:
            existing[g["corp_code"]] = g

    def fnorm(d):
        return _json.dumps([d.get("acct"), d.get("idx"), d.get("currency"), d.get("rcept_no")],
                           sort_keys=True, ensure_ascii=False)

    return [r for r in rows
            if r["corp_code"] not in existing or fnorm(existing[r["corp_code"]]) != fnorm(r)]


# ── 이벤트 (15분) — 증분: 새 공시(rcept_no)만 처리 ──────────────
def _state_file():
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    return config.OUTPUT_DIR / "last_rcept.txt"


def _load_last_rcept():
    f = _state_file()
    return f.read_text().strip() if f.exists() else ""


def _enum_recent(bgn, end):
    """list.json B/C/D 최근 제출(rcept_no 포함) → 필링 리스트 + 열거콜수. (창<3개월=청킹불요)"""
    out, calls = [], 0
    for ty in ("B", "C", "D"):
        page = 1
        while True:
            try:
                data = dart_api.list_disclosures(bgn, end, pblntf_ty=ty, page_no=page, page_count=100)
            except Exception:
                break
            calls += 1
            if data.get("status") == "013":
                break
            for it in data.get("list", []):
                if it.get("corp_code"):
                    out.append({"corp": it["corp_code"], "rnm": (it.get("report_nm") or "").strip(),
                                "rcpt": str(it.get("rcept_no", "")), "ty": ty,
                                "name": it.get("corp_name", ""), "cls": it.get("corp_cls", ""),
                                "stock": (it.get("stock_code") or "").strip()})
            tp = int(data.get("total_page", 1) or 1)
            if page >= tp:
                break
            page += 1
            time.sleep(config.REQUEST_SLEEP)
    return out, calls


def run_events(lookback: int, resweep: bool = False):
    # 재훑기는 둘째 키로 — 워커(키#1) 일일예산을 침범하지 않게. 키#2 미설정 시 키#1 폴백.
    # (자정 직후 크론으로 돌리면 백필 rollover가 키#2에 닿기 전이라 한도 여유)
    if resweep and config.DART_API_KEY_2:
        config.DART_API_KEY = config.DART_API_KEY_2
        log("재훑기: 둘째 키(키#2) 사용")
    today = datetime.now(KST).date()
    yr = str(today.year)
    bgn_y, end_y = f"{yr}0101", f"{yr}1231"
    bgn = (today - timedelta(days=lookback)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    filings, enum_calls = _enum_recent(bgn, end)
    last = _load_last_rcept()
    # resweep(재훑기): 마커 무시하고 창 내 '전체'를 다시 받음 — OpenDART 구조화 데이터 시차로
    #   처음 받을 때 비어/옛값이었던 공시를 뒤늦게 보정(목록은 즉시, 표 데이터는 수시간 지연).
    #   변화 없는 재확인이 대부분이라 write-on-change로 기록(=사이트 재빌드)은 실제 변경분만.
    # 평소(증분): 직전 max 이후 신규만(저비용). 마커는 평소 모드만 전진.
    target = filings if resweep else [f for f in filings if f["rcpt"] > last]
    cur_max = max((f["rcpt"] for f in filings), default=last)
    log(f"최근 {lookback}일 공시 {len(filings)} · {'재훑기' if resweep else '신규'} {len(target)} (직전 max {last or '없음'})")

    # 대상 공시 → (회사, 엔드포인트) 매칭 (구조화 API 없는 유형 skip)
    work, meta = {}, {}
    for f in target:
        meta[f["corp"]] = {"name": f["name"], "cls": f["cls"], "stock": f["stock"]}
        gname, matcher, table, needs_range = registry.GROUP[f["ty"]]
        ep = matcher(f["rnm"])
        if not ep:
            continue
        if (f["corp"], ep) not in work:
            label = next((l for (e, l, k) in table if e == ep), ep)
            work[(f["corp"], ep)] = {"grp": gname, "label": label, "needs_range": needs_range}

    # 해당 회사의 현재연도 전체 재수집 → events 행 (period=연도/snapshot)
    rows, calls = [], 0
    for (corp, ep), info in work.items():
        params = {"corp_code": corp}
        period = "snapshot"
        if info["needs_range"]:
            params.update(bgn_de=bgn_y, end_de=end_y)
            period = yr
        try:
            data = dart_api._get(f"{config.API_BASE}/{ep}.json", params)
        except Exception:
            continue
        calls += 1
        r_rows = data.get("list") or []
        groups = data.get("group") or []
        n = len(r_rows) + sum(len(g.get("list") or []) for g in groups)
        if not n:
            continue
        m = meta.get(corp, {})
        d = {k: v for k, v in (("rows", r_rows), ("groups", groups)) if v}
        rows.append({"endpoint": ep, "corp_code": corp, "period": period, "grp": info["grp"],
                     "label": info["label"], "corp_name": m.get("name", ""),
                     "stock": m.get("stock", ""), "cls": m.get("cls", ""), "n": n, "data": d})
        time.sleep(config.REQUEST_SLEEP)

    if resweep:
        rows = _changed_only("events", rows)                       # 실제 바뀐(또는 새) 건만 기록
    _batch_upsert("events", rows, "endpoint,corp_code,period")
    if not resweep and cur_max and cur_max > last:                 # 마커는 평소 모드만 전진(재훑기는 무전진)
        _state_file().write_text(cur_max)                          # 진행 마커 저장
    log(f"events {'재훑기' if resweep else 'upsert'} {len(rows)}건 기록 (열거 {enum_calls} + 데이터 {calls}콜)")

    # 성공 시 healthchecks.io ping (공시 0건이어도 매번 — 심장박동). URL은 서버 .env에만.
    if config.HEALTHCHECK_URL:
        try:
            requests.get(config.HEALTHCHECK_URL, timeout=10)
            log("healthcheck ping ✓")
        except Exception:
            pass


# ── 재무 (일 1회) ──────────────────────────────────────────────
def _parse_amt(s):
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return int(t)
    except ValueError:
        try:
            return int(float(t))
        except ValueError:
            return None


def run_financials():
    today = datetime.now(KST).date()
    yr = str(today.year)
    # 올해 정기공시 제출자 = 유니버스 (상폐 포함, 신규 포함)
    corps, _rpt, _p, _f, _c = enm.enum_type("A", date(today.year, 1, 1), today)
    codes = sorted(corps)
    log(f"재무 갱신: {yr} 제출자 {len(codes):,}개사")
    calls = 0
    for reprt in config.REPRT_ALL:
        period = {}
        for i in range(0, len(codes), config.MULTI_BATCH):
            batch = codes[i:i + config.MULTI_BATCH]
            try:
                ar = dart_api.get_multi_account(batch, yr, reprt)
            except Exception:
                ar = []
            calls += 1
            for r in ar:
                c, fs, nm = r.get("corp_code"), r.get("fs_div"), r.get("account_nm")
                if not c or fs not in ("CFS", "OFS") or not nm:
                    continue
                rec = period.setdefault(c, _blank_fin(c, corps, yr, reprt))
                rec["acct"][fs].setdefault(nm, _parse_amt(r.get("thstrm_amount")))
                if not rec["rcept_no"]:
                    rec["rcept_no"] = r.get("rcept_no", "")
                cur = (r.get("currency") or "").strip()
                if (len(cur) == 3 and cur.isascii() and cur.isalpha()
                        and cur.upper() != "KRW" and (fs == "CFS" or rec["currency"] == "KRW")):
                    rec["currency"] = cur.upper()
            for cat, code in config.IDX_CL_CODE.items():       # 재무지표(2023 3분기~)
                try:
                    ir = dart_api.get_company_index(batch, yr, reprt, code)
                except Exception:
                    ir = []
                calls += 1
                for r in ir:
                    c, inm, val = r.get("corp_code"), r.get("idx_nm"), r.get("idx_val")
                    if not c or not inm or not (val or "").strip():
                        continue
                    rec = period.setdefault(c, _blank_fin(c, corps, yr, reprt))
                    sd = r.get("stlm_dt", "")
                    if sd:
                        rec["stlm_dt"] = sd
                    rec["idx"].setdefault(cat, {})[inm] = val
            time.sleep(config.REQUEST_SLEEP)
        rows = [r for r in period.values() if r["acct"]["CFS"] or r["acct"]["OFS"] or r["idx"]]
        fetched = len(rows)
        rows = _changed_fin(rows)   # 변경분만 upsert — updated_at churn 방지(Disclo 증분 빌드 복원)
        _batch_upsert("financials", rows, "corp_code,year,reprt", 500)
        log(f"  {yr} {config.REPRT_NM[reprt]}: {len(rows)}/{fetched}건 upsert (변경분만, 누적 {calls}콜)")
    _reconcile_companies(codes)   # 재무 들어온 회사는 회사개황(종목명)도 즉시 확보(누락 방지)


def _reconcile_companies(codes):
    """이번 재무 갱신 대상(codes) 중 companies(회사개황) 없는 회사를 즉시 보완.
    회사개황 잡은 주 1회(일요일)뿐이라, 신규 제출자의 종목명 누락(최대 6일)을 매 재무런마다 메운다.
    대개 누락 0~소수 → companies 코드 조회 + 소수 회사개황 콜로 저비용."""
    try:
        have = {c["corp_code"] for c in sb.get_all("companies?select=corp_code")}
    except Exception as e:
        log(f"회사개황 보완 skip(companies 조회 실패): {e}")
        return
    missing = [c for c in codes if c not in have]
    if not missing:
        log("회사개황 보완: 누락 없음")
        return
    today = datetime.now(KST).strftime("%Y-%m-%d")
    rows = []
    for corp in missing:
        try:
            p = dart_api.get_company(corp)
        except Exception:
            p = {}
        if p:
            p["_fetched"] = today
            rows.append({"corp_code": corp, "corp_name": p.get("corp_name"),
                         "corp_name_eng": p.get("corp_name_eng"), "stock_code": p.get("stock_code"),
                         "ceo_nm": p.get("ceo_nm"), "corp_cls": p.get("corp_cls"),
                         "induty_code": p.get("induty_code"), "est_dt": p.get("est_dt"),
                         "acc_mt": p.get("acc_mt"), "adres": p.get("adres"), "profile": p})
        time.sleep(config.REQUEST_SLEEP)
    _batch_upsert("companies", rows, "corp_code", 500)
    log(f"회사개황 보완: {len(rows)}/{len(missing)}개사 신규 확보(종목명 포함)")


def _blank_fin(corp, corps, yr, reprt):
    m = corps.get(corp, {})
    mm = config.REPRT_STLM.get(reprt, "12-31")
    return {"corp_code": corp, "corp_name": m.get("name", ""), "stock": m.get("stock", ""),
            "year": yr, "reprt": reprt, "reprt_nm": config.REPRT_NM.get(reprt, reprt),
            "stlm_dt": f"{yr}-{mm}", "rcept_no": "", "currency": "KRW",
            "acct": {"CFS": {}, "OFS": {}}, "idx": {}}


# ── 기업개황 (선택, 일 1회) ─────────────────────────────────────
def run_companies():
    today = datetime.now(KST).date()
    corps, _rpt, _p, _f, _c = enm.enum_type("A", date(today.year, 1, 1), today)
    rows, calls = [], 0
    for corp in sorted(corps):
        try:
            p = dart_api.get_company(corp)
        except Exception:
            p = {}
        calls += 1
        if p:
            rows.append({"corp_code": corp, "corp_name": p.get("corp_name"),
                         "corp_name_eng": p.get("corp_name_eng"), "stock_code": p.get("stock_code"),
                         "ceo_nm": p.get("ceo_nm"), "corp_cls": p.get("corp_cls"),
                         "induty_code": p.get("induty_code"), "est_dt": p.get("est_dt"),
                         "acc_mt": p.get("acc_mt"), "adres": p.get("adres"), "profile": p})
        time.sleep(config.REQUEST_SLEEP)
    _batch_upsert("companies", rows, "corp_code", 500)
    log(f"companies upsert {len(rows)}건 ({calls}콜)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["events", "financials", "companies", "resweep"])
    ap.add_argument("--lookback", type=int, default=3,
                    help="events: 최근 N일 / resweep: 재훑기 창(일). 일일 그물=7, catch-up=넓게(예 14)")
    args = ap.parse_args()
    t0 = time.time()
    log(f"=== update {args.mode} 시작 ===")
    if args.mode == "events":
        run_events(args.lookback)
    elif args.mode == "resweep":
        run_events(args.lookback, resweep=True)   # 마커 무시 재훑기(시차 누락 보정) + write-on-change
    elif args.mode == "financials":
        run_financials()
    else:
        run_companies()
    log(f"=== update {args.mode} 완료 ({time.time()-t0:.0f}s) ===")


if __name__ == "__main__":
    main()
