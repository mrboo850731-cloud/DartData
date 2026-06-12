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


_H = {"apikey": config.SUPABASE_SERVICE_ROLE_KEY,
      "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}"}


def _fetch_worklist(years):
    base = config.SUPABASE_URL + "/rest/v1/financials"
    yf = "in.(" + ",".join(years) + ")"
    out, off, page = [], 0, 1000
    while True:
        r = requests.get(base, headers={**_H, "Range": f"{off}-{off + page - 1}"},
                         params={"select": "corp_code,corp_name,stock,year,reprt,reprt_nm",
                                 "year": yf, "order": "year.desc,corp_code.asc,reprt.asc"}, timeout=60)  # 유일정렬(페이지중복 방지)
        b = r.json()
        out.extend(b)
        if len(b) < page:
            break
        off += page
    return out


def _fetch_done(years):
    base = config.SUPABASE_URL + "/rest/v1/periodic_info"
    yf = "in.(" + ",".join(years) + ")"
    done, off, page = set(), 0, 1000
    while True:
        try:
            r = requests.get(base, headers={**_H, "Range": f"{off}-{off + page - 1}"},
                             params={"select": "corp_code,year,reprt", "year": yf}, timeout=60)
            if r.status_code >= 400:
                break
            b = r.json()
        except Exception:
            break
        if not isinstance(b, list):
            break
        for x in b:
            done.add((x["corp_code"], str(x["year"]), x["reprt"]))
        if len(b) < page:
            break
        off += page
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help='"2026" 또는 "2023-2026"')
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--max-calls", type=int, default=0)
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
    calls = empties = total_rows = total_bytes = 0
    stopped = False
    for i, w in enumerate(todo, 1):
        corp, yr, reprt = w["corp_code"], str(w["year"]), w["reprt"]
        data, nrows = {}, 0
        for ep, _label in config.DS002_ENDPOINTS:
            if a.max_calls and calls >= a.max_calls:
                stopped = True
                break
            try:
                lst, grp = dart_api.get_periodic(ep, corp, yr, reprt)
            except dart_api.DartApiError as e:
                if "020" in str(e):
                    log("⚠ 일일한도(020) 도달 → 중단 (재개 가능)")
                    stopped = True
                    break
                lst, grp = [], []
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
        if not data:
            continue
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
                sb.upsert("periodic_info", buf, "corp_code,year,reprt")
                buf, buf_bytes = [], 0
        if i % 100 == 0:
            log(f"  {i}/{len(todo)} (콜 {calls}, 저장 {built})")
        time.sleep(config.REQUEST_SLEEP)

    if not a.measure and buf:
        sb.upsert("periodic_info", buf, "corp_code,year,reprt")

    log(f"=== {'측정' if a.measure else '수집'} 종료 ({time.time() - t0:.0f}s) ===")
    log(f"콜 {calls} · 보고서 {built} · 빈항목(013) {empties} · 총행 {total_rows:,}")
    if built:
        log(f"보고서당 평균: 항목데이터 {total_rows/built:.0f}행 · JSON {total_bytes/built/1024:.1f}KB · 콜 {calls/built:.0f}")
        for nm, n in (("2025~2026", 14900), ("2023~2026", 37000), ("전체2015~", 96900)):
            log(f"  → {nm}: ~{n*len(config.DS002_ENDPOINTS):,}콜 · ~{total_bytes/built*n/1024/1024:.0f}MB")


if __name__ == "__main__":
    main()
