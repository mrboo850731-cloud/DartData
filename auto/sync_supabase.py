"""로컬 JSON → Supabase 업서트: financials · events(전 연도) · companies.

PK 기준 merge-duplicates 라 재실행해도 안전(중복 없음).
실행:
  python auto/sync_supabase.py            # 전부
  python auto/sync_supabase.py financials # 특정 테이블만 (financials|events|companies)
"""
from __future__ import annotations
import sys
import json
import glob
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config
import supabase_client as sb

OUT = config.OUTPUT_DIR


def _load(p):
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    return d if isinstance(d, list) else d.get("records", d.get("companies", []))


def _batches(rows, n):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def _push(table, rows, on_conflict, batch):
    total = len(rows)
    done = 0
    for b in _batches(rows, batch):
        sb.upsert(table, b, on_conflict)
        done += len(b)
        print(f"    {table}: {done:,}/{total:,}", flush=True)
        time.sleep(0.05)
    return total


def sync_financials():
    recs = _load(OUT / "financials.json")
    rows = [{
        "corp_code": r["corp_code"], "year": r["year"], "reprt": r["reprt"],
        "corp_name": r.get("corp_name"), "stock": r.get("stock"),
        "reprt_nm": r.get("reprt_nm"), "stlm_dt": r.get("stlm_dt"),
        "rcept_no": r.get("rcept_no"), "currency": r.get("currency", "KRW"),
        "acct": r.get("acct"), "idx": r.get("idx"),
    } for r in recs]
    print(f"  financials: {len(rows):,}건 업서트…", flush=True)
    return _push("financials", rows, "corp_code,year,reprt", 500)


def sync_events():
    files = [f for f in sorted(glob.glob(str(OUT / "events_*.json")))
             if "sample" not in os.path.basename(f) and "diag" not in os.path.basename(f)]
    seen = {}                                  # (endpoint,corp,period) → row (파일순 → 최신 우선 덮기)
    for f in files:
        for r in _load(f):
            p = r.get("params", {})
            period = (f"{p['bgn_de']}-{p['end_de']}" if p.get("bgn_de") else "snapshot")
            data = {k: r[k] for k in ("rows", "groups") if k in r}
            seen[(r["endpoint"], r["corp_code"], period)] = {
                "endpoint": r["endpoint"], "corp_code": r["corp_code"], "period": period,
                "grp": r.get("group"), "label": r.get("label"),
                "corp_name": r.get("corp_name"), "stock": r.get("stock"),
                "cls": r.get("cls"), "n": r.get("n"), "data": data,
            }
    rows = list(seen.values())
    print(f"  events: {len(rows):,}건 (파일 {len(files)}개 병합) 업서트…", flush=True)
    return _push("events", rows, "endpoint,corp_code,period", 200)


def sync_companies():
    recs = _load(OUT / "companies.json")
    rows = [{
        "corp_code": c.get("corp_code"), "corp_name": c.get("corp_name"),
        "corp_name_eng": c.get("corp_name_eng"), "stock_code": c.get("stock_code"),
        "ceo_nm": c.get("ceo_nm"), "corp_cls": c.get("corp_cls"),
        "induty_code": c.get("induty_code"), "est_dt": c.get("est_dt"),
        "acc_mt": c.get("acc_mt"), "adres": c.get("adres"), "profile": c,
    } for c in recs if c.get("corp_code")]
    print(f"  companies: {len(rows):,}건 업서트…", flush=True)
    return _push("companies", rows, "corp_code", 500)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    t0 = time.time()
    jobs = {"financials": sync_financials, "events": sync_events, "companies": sync_companies}
    targets = jobs if which == "all" else {which: jobs[which]}
    for name, fn in targets.items():
        print(f"▶ {name}", flush=True)
        fn()
    print(f"\n완료 ({time.time()-t0:.0f}s). 행 수 확인:")
    for t in (targets if which != "all" else jobs):
        try:
            print(f"  {t}: {sb.count(t):,} rows")
        except Exception as e:
            print(f"  {t}: count 실패 {e}")


if __name__ == "__main__":
    main()
