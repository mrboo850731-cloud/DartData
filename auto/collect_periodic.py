"""DS002 정기보고서 주요정보 수집 → Supabase periodic_info (회사·기간당 JSONB 1행, 30개 항목).

worklist = Supabase `financials`의 (corp,year,reprt) = 실제 정기보고서. 보고서 1건당 **30개 API 호출**
(다수가 013 빈값 — 그래도 호출). 데이터 있는 항목만 data JSONB에 모아 1행 upsert.
newest-first(year.desc)·resume(완료분 skip)·020/max-calls 중단(부분 보고서 버림). financials_full 패턴.

사용:
  python collect_periodic.py --years 2025-2026
  python collect_periodic.py --years 2023-2026 --max-calls 36000
  python collect_periodic.py --years 2025-2026 --sample 30 --measure   # 업서트X·콜측정
"""
from __future__ import annotations
import sys
import time
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import requests
import config
import dart_api
import supabase_client as sb

KST = timezone(timedelta(hours=9))


def log(m):
    print(f"[{datetime.now(KST):%m-%d %H:%M:%S}] {m}", flush=True)


def _upsert_retry(table, rows, conflict, tries=40, wait=45):
    """Supabase 일시장애(522/5xx/DNS 등) 견딤: 실패 시 대기·재시도(최대 ~30분). 끝내 실패면 False(중단·resume)."""
    for k in range(tries):
        try:
            sb.upsert(table, rows, conflict)
            return True
        except Exception as e:
            log(f"⚠ 업서트 실패 {k + 1}/{tries} ({str(e)[:90]}) — {wait}s 후 재시도")
            time.sleep(wait)
    return False


_H = {"apikey": config.SUPABASE_SERVICE_ROLE_KEY,
      "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}"}


def _fetch_worklist(years):
    base = config.SUPABASE_URL + "/rest/v1/financials"
    yf = "in.(" + ",".join(years) + ")"
    out, off, page = [], 0, 1000
    while True:
        r = requests.get(base, headers={**_H, "Range": f"{off}-{off + page - 1}"},
                         params={"select": "corp_code,corp_name,stock,year,reprt,reprt_nm",
                                 "year": yf, "order": "year.desc,reprt.asc,corp_code.asc"}, timeout=60)  # 유일정렬(페이지중복 방지)
        b = r.json()
        out.extend(b)
        if len(b) < page:
            break
        off += page
    # 모든 단계 최신 우선 재정렬: 연도 desc → 연도내 최신 보고서순(사업>3분기>반기>1분기, 제출 늦은순) → corp.
    # (보고서 코드가 이 순서로 정렬 안 되므로 파이썬 커스텀 우선순위로 처리)
    _RP = {"11011": 0, "11014": 1, "11012": 2, "11013": 3}
    out.sort(key=lambda w: (-int(w["year"]), _RP.get(w["reprt"], 9), w["corp_code"]))
    return out


def _fetch_done(years):
    """이미 받은 periodic_info (corp,year,reprt) 집합(resume).
    연도별 조회(작은 offset=빠름)+유일정렬+페이지 재시도. 끝내 못 받으면 raise(중단).
    (정렬없는 전체 offset 페이지네이션이 느려 끊기면 done 누락→끝난 보고서 30콜 재호출 낭비)"""
    base = config.SUPABASE_URL + "/rest/v1/periodic_info"
    done = set()
    for y in years:
        off = 0
        while True:
            b = None
            for _ in range(6):
                try:
                    r = requests.get(base, headers={**_H, "Range": f"{off}-{off + 999}"},
                                     params={"select": "corp_code,year,reprt", "year": f"eq.{y}",
                                             "order": "corp_code.asc,reprt.asc"}, timeout=90)
                    if r.status_code in (200, 206):
                        b = r.json()
                        break
                    if r.status_code == 416:        # 범위초과 = 더 없음
                        b = []
                        break
                except Exception:
                    pass
                time.sleep(5)
            if not isinstance(b, list):
                raise RuntimeError(f"done-set({y}) 조회 실패 — 중단(불완전 done으로 재호출 방지, 다음 실행 재시도)")
            for x in b:
                done.add((x["corp_code"], str(x["year"]), x["reprt"]))
            if len(b) < 1000:
                break
            off += 1000
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help='"2026" 또는 "2023-2026"')
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--max-calls", type=int, default=0, help="키#1 예산(0=무제한). 소진 시 키#2로 롤오버")
    ap.add_argument("--key2-max", type=int, default=40000, help="키#2(DART_API_KEY_2) 예산 — 롤오버 후 한도")
    ap.add_argument("--measure", action="store_true")
    a = ap.parse_args()

    if "-" in a.years:
        x, y = a.years.split("-")
        years = [str(yy) for yy in range(int(x), int(y) + 1)]
    else:
        years = [a.years]

    t0 = time.time()
    wl = _fetch_worklist(years)
    log(f"worklist {len(wl):,}건 (연도 {','.join(years)})")
    done = set() if a.measure else _fetch_done(years)
    if done:
        log(f"이미 받음 {len(done):,}건 → skip")
    todo = [w for w in wl if (w["corp_code"], str(w["year"]), w["reprt"]) not in done]
    if a.sample:
        todo = todo[:a.sample]
    log(f"처리대상 {len(todo):,}건 · 예상 콜 ~{len(todo) * len(config.DS002_ENDPOINTS):,}")

    buf, built, buf_bytes = [], 0, 0
    calls = calls2 = empties = total_rows = total_bytes = 0
    stopped = False
    # 둘째 키 롤오버: 키#1 예산(--max-calls) 소진 또는 020 시 키#2(DART_API_KEY_2)로 전환해 이어감.
    # dart_api는 매 호출 config.DART_API_KEY를 읽으므로 값만 바꾸면 즉시 전환. 키가 같거나 미설정이면 단일키.
    KEY2 = config.DART_API_KEY_2
    ROLLOVER = bool(KEY2 and KEY2 != config.DART_API_KEY)
    on_key2 = False
    if ROLLOVER:
        log(f"롤오버 활성 — 키#1 {a.max_calls or '무제한'}콜 → 키#2 {a.key2_max}콜")
    for i, w in enumerate(todo, 1):
        corp, yr, reprt = w["corp_code"], str(w["year"]), w["reprt"]
        data, nrows = {}, 0
        for ep, _label in config.DS002_ENDPOINTS:
            if not on_key2 and a.max_calls and calls >= a.max_calls:
                if ROLLOVER:
                    config.DART_API_KEY = KEY2; on_key2 = True
                    log(f"키#1 예산({a.max_calls}콜) 소진 → 키#2로 전환")
                else:
                    stopped = True
                    break
            if on_key2 and calls2 >= a.key2_max:
                log(f"키#2 예산({a.key2_max}콜) 소진 → 중단 (재개 가능)")
                stopped = True
                break
            try:
                lst, grp = dart_api.get_periodic(ep, corp, yr, reprt)
            except dart_api.DartApiError as e:
                if "020" in str(e):
                    if not on_key2 and ROLLOVER:
                        config.DART_API_KEY = KEY2; on_key2 = True
                        log("키#1 일한도(020) → 키#2로 전환")
                        try:
                            lst, grp = dart_api.get_periodic(ep, corp, yr, reprt)
                        except dart_api.DartApiError as e2:
                            if "020" in str(e2):
                                log("키#2도 일한도(020) → 중단 (재개 가능)")
                                stopped = True
                                break
                            lst, grp = [], []
                    else:
                        log("⚠ 일일한도(020) 도달 → 중단 (재개 가능)")
                        stopped = True
                        break
                else:
                    lst, grp = [], []
            if on_key2:
                calls2 += 1
            else:
                calls += 1
            if lst:
                data[ep] = lst
                nrows += len(lst)
            elif grp:
                data[ep] = {"g": grp}
                nrows += sum(len(g.get("list") or []) for g in grp)
            else:
                empties += 1
        if stopped:                       # 부분 수집된 현재 보고서는 버림 → 재실행 시 통째 재수집
            break
        # 모든 항목 013(빈)이어도 마커 저장 → done 처리 (안 하면 매 실행 재호출·todo가 0이 안 됨)
        row = {"corp_code": corp, "corp_name": w.get("corp_name", ""), "stock": w.get("stock", ""),
               "year": yr, "reprt": reprt, "reprt_nm": w.get("reprt_nm", ""),
               "n_topics": len(data), "n_rows": nrows, "data": data}
        built += 1
        total_rows += nrows
        b = len(json.dumps(row, ensure_ascii=False).encode())
        total_bytes += b
        if not a.measure:
            buf.append(row)
            buf_bytes += b
            if buf_bytes > 3_000_000 or len(buf) >= 100:
                if not _upsert_retry("periodic_info", buf, "corp_code,year,reprt"):
                    log("⚠ Supabase 장기장애 → 중단(다음 실행 시 resume)")
                    stopped = True
                    break
                buf, buf_bytes = [], 0
        if i % 100 == 0:
            log(f"  {i}/{len(todo)} (콜 키#1 {calls}{f'+키#2 {calls2}' if calls2 else ''}, 저장 {built})")
        time.sleep(config.REQUEST_SLEEP)

    if not a.measure and buf:
        _upsert_retry("periodic_info", buf, "corp_code,year,reprt")

    log(f"=== {'측정' if a.measure else '수집'} 종료 ({time.time() - t0:.0f}s) ===")
    log(f"콜 키#1 {calls} + 키#2 {calls2} = {calls + calls2} · 보고서 {built} · 빈항목(013) {empties} · 총행 {total_rows:,}")
    if built:
        log(f"보고서당 평균: 항목데이터 {total_rows/built:.0f}행 · JSON {total_bytes/built/1024:.1f}KB · 콜 {calls/built:.0f}")
        for nm, n in (("2025~2026", 14900), ("2023~2026", 37000), ("전체2015~", 96900)):
            log(f"  → {nm}: ~{n*len(config.DS002_ENDPOINTS):,}콜 · ~{total_bytes/built*n/1024/1024:.0f}MB")


if __name__ == "__main__":
    main()
