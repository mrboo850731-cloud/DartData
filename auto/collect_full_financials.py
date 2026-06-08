"""DartData 전체재무제표(전 계정) 수집 → Supabase financials_full upsert.

worklist = Supabase `financials`(주요계정)의 (corp,year,reprt) → 실제 존재 조합만 호출(헛콜 0).
각 (corp,year,reprt)마다 CFS(연결)·OFS(별도) 둘 다 fnlttSinglAcntAll → 전 계정 저장.
PK(corp_code,year,reprt) 병합 upsert, 이미 받은 건 skip(resume). 일일한도(020) 만나면 중단(재개 가능).

사용:
  python collect_full_financials.py --years 2026
  python collect_full_financials.py --years 2015-2026
  python collect_full_financials.py --years 2026 --sample 100 --measure   # 측정만(업서트X·테이블 불요)
  python collect_full_financials.py --years 2026 --max-calls 1000          # 콜 상한
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


def _fetch_worklist(years):
    """financials 에서 (corp,year,reprt)+메타 페이지네이션 수집."""
    base = config.SUPABASE_URL + "/rest/v1/financials"
    yf = "in.(" + ",".join(years) + ")"
    out, off, page = [], 0, 1000
    while True:
        r = requests.get(base, headers={**_H, "Range": f"{off}-{off + page - 1}"},
                         params={"select": "corp_code,corp_name,stock,year,reprt,reprt_nm,stlm_dt",
                                 "year": yf, "order": "year.desc,corp_code.asc"}, timeout=60)  # 최신 연도부터
        b = r.json()
        out.extend(b)
        if len(b) < page:
            break
        off += page
    return out


def _fetch_done(years):
    """이미 받은 financials_full (corp,year,reprt) 집합(resume). 테이블 없으면 빈 set."""
    base = config.SUPABASE_URL + "/rest/v1/financials_full"
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


def _trim(items):
    """전 계정 행 → 압축 보관(상수필드 제거, 금액 정수화)."""
    out = []
    for r in items:
        out.append({"sj": r.get("sj_div"), "id": r.get("account_id"), "nm": r.get("account_nm"),
                    "v": _parse_amt(r.get("thstrm_amount")),
                    "pv": _parse_amt(r.get("frmtrm_amount")),
                    "ppv": _parse_amt(r.get("bfefrmtrm_amount")), "o": r.get("ord")})
    return out


def _currency(items, cur):
    """통화원칙: 3자리 ISO alpha·비KRW면 라벨 채택(CFS 우선 호출 순서로 들어옴)."""
    for r in items:
        c = (r.get("currency") or "").strip().upper()
        if len(c) == 3 and c.isascii() and c.isalpha() and c != "KRW":
            return c
    return cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", required=True, help='"2026" 또는 "2015-2026"')
    ap.add_argument("--sample", type=int, default=0, help="앞에서 N건만")
    ap.add_argument("--max-calls", type=int, default=0, help="콜 상한(0=무제한)")
    ap.add_argument("--measure", action="store_true", help="업서트 없이 콜·용량 측정만")
    args = ap.parse_args()

    if "-" in args.years:
        a, b = args.years.split("-")
        years = [str(y) for y in range(int(a), int(b) + 1)]
    else:
        years = [args.years]

    t0 = time.time()
    wl = _fetch_worklist(years)
    log(f"worklist {len(wl):,}건 (연도 {','.join(years)})")
    done = set() if args.measure else _fetch_done(years)
    if done:
        log(f"이미 받음 {len(done):,}건 → skip")
    todo = [w for w in wl if (w["corp_code"], str(w["year"]), w["reprt"]) not in done]
    if args.sample:
        todo = todo[:args.sample]
    log(f"처리대상 {len(todo):,}건 · 예상 콜 ~{len(todo) * 2:,}")

    rows, buf = [], []
    calls = empties = built = total_items = total_bytes = 0
    stopped = False
    for i, w in enumerate(todo, 1):
        corp, yr, reprt = w["corp_code"], str(w["year"]), w["reprt"]
        stmt, cur, rcpt = {}, "KRW", ""
        for fs in config.FS_DIV:
            if args.max_calls and calls >= args.max_calls:
                stopped = True
                break
            try:
                items = dart_api.get_single_account_all(corp, yr, reprt, fs)
            except dart_api.DartApiError as e:
                if "020" in str(e):
                    log("⚠ 일일한도(020) 도달 → 중단 (내일 재개)")
                    stopped = True
                    break
                items = []
            calls += 1
            if not items:
                empties += 1
                continue
            stmt[fs] = _trim(items)
            total_items += len(items)
            cur = _currency(items, cur)
            if not rcpt:
                rcpt = items[0].get("rcept_no", "")
        if stopped:                       # 한도(020)/상한 도달 → 부분 수집된 현재 회사는 '버리고' 중단
            break                         # (DB에 안 넣음) → 내일 같은 명령 재실행 시 그 회사부터 통째 재수집 = 반쪽 행 0, 정확한 resume
        if stmt:
            row = {"corp_code": corp, "corp_name": w.get("corp_name", ""), "stock": w.get("stock", ""),
                   "year": yr, "reprt": reprt, "reprt_nm": w.get("reprt_nm", ""),
                   "stlm_dt": w.get("stlm_dt", ""), "rcept_no": rcpt, "currency": cur,
                   "n_cfs": len(stmt.get("CFS", [])), "n_ofs": len(stmt.get("OFS", [])), "stmt": stmt}
            built += 1
            total_bytes += len(json.dumps(row, ensure_ascii=False).encode())
            if not args.measure:
                buf.append(row)
                if len(buf) >= 200:
                    sb.upsert("financials_full", buf, "corp_code,year,reprt")
                    buf = []
        if i % 200 == 0:
            log(f"  {i}/{len(todo)} (콜 {calls}, 저장 {built})")
        time.sleep(config.REQUEST_SLEEP)

    if not args.measure and buf:
        sb.upsert("financials_full", buf, "corp_code,year,reprt")

    log(f"=== {'측정' if args.measure else '수집'} 종료 ({time.time() - t0:.0f}s) ===")
    log(f"콜 {calls} · 저장행 {built} · 빈(013) {empties} · 전계정행 {total_items:,}")
    if built:
        ai = total_items / built
        ab = total_bytes / built
        log(f"행당 평균: 계정 {ai:.0f}개 · JSON {ab/1024:.1f} KB")
        for nm, n in (("2026", 2931), ("2023~2026", 37355), ("전체 2015~2026", 96889)):
            log(f"  → {nm}: {n:,}행 ≈ {ab*n/1024/1024:.0f} MB 저장, ~{n*2:,}콜")


if __name__ == "__main__":
    main()
