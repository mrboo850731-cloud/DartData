"""DS003 재무 백필 — 다중회사 주요계정·재무지표를 DartData가 자체 수집(FilingHub 독립).

기존 financials.json(=FilingHub 병합본)의 회사 유니버스를 재사용해, 지정 연도의 재무를
다중회사 API(100개 묶음)로 모아 financials.json 에 additive 병합(PK: corp_code,year,reprt).
  - 주요계정(fnlttMultiAcnt): 전 연도 가능
  - 재무지표(fnlttCmpnyIndx): 2023 3분기~만 존재 → year<2023 은 호출 스킵(콜 절약)
  - 통화: CFS 우선 + ISO 3자리 화이트리스트 (dart-currency-doctrine)
  - 연도별 체크포인트 저장(중간에 죽어도 완료 연도분 보존)

실행:
  python auto/collect_financials.py 2022 2021 2020   # 연도들 백필
  python auto/collect_financials.py 2024 --limit 200 # 처음 200개사만(검증)
"""
from __future__ import annotations
import sys
import json
import time
import os
import argparse
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config
import dart_api
import enumerate_filings as enm

KST = timezone(timedelta(hours=9))
FIN = config.OUTPUT_DIR / "financials.json"
FILERS_ALL = config.OUTPUT_DIR / "filers_all.json"


def ensure_universe(bgn: date, end: date) -> dict:
    """[bgn,end] 기간 정기공시(A) 제출자 = 그 시기 재무 보유 가능 회사(상장폐지사 포함).

    역사 완전 백필의 핵심: 현재 회사 명단이 아니라 '그 시절 제출자'를 유니버스로.
    filers_all.json 에 캐시; 요청 범위를 커버하면 재열거 없이 재사용.
    """
    if FILERS_ALL.exists():
        try:
            c = json.loads(FILERS_ALL.read_text(encoding="utf-8"))
            if c.get("bgn", "9") <= bgn.isoformat() and c.get("end", "0") >= end.isoformat():
                print(f"제출자 캐시 재사용: {c['count']:,}개사 ({c['bgn']}~{c['end']})", flush=True)
                return c["filers"]
        except Exception:
            pass
    print(f"정기공시 제출자 열거 {bgn}~{end} (1회성, 캐시 저장)…", flush=True)
    corps, _rpt, _pairs, _f, calls = enm.enum_type("A", bgn, end)
    filers = {code: {"name": v.get("name", ""), "stock": v.get("stock", ""), "cls": v.get("cls", "")}
              for code, v in corps.items()}
    FILERS_ALL.write_text(json.dumps(
        {"bgn": bgn.isoformat(), "end": end.isoformat(), "count": len(filers), "filers": filers},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  제출자 {len(filers):,}개사 · 열거 {calls}콜 → filers_all.json", flush=True)
    return filers


def parse_amount(s):
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


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def load_fin():
    if FIN.exists():
        d = json.loads(FIN.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else d.get("records", [])
    return []


def save_fin(records):
    tmp = FIN.with_name(FIN.name + ".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, FIN)


def _blank(corp, uni, year, reprt, today):
    m = uni.get(corp, {})
    mm = config.REPRT_STLM.get(reprt, "12-31")
    return {"corp_code": corp, "corp_name": m.get("name", ""), "stock": m.get("stock", ""),
            "year": year, "reprt": reprt, "reprt_nm": config.REPRT_NM.get(reprt, reprt),
            "stlm_dt": f"{year}-{mm}", "rcept_no": "", "currency": "KRW",
            "acct": {"CFS": {}, "OFS": {}}, "idx": {}, "_fetched": today}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("years", nargs="+", type=int, help="백필 연도들")
    ap.add_argument("--limit", type=int, default=None, help="처음 N개사만 (검증)")
    args = ap.parse_args()

    today_d = datetime.now(KST).date()
    existing = load_fin()
    uni = {}
    for r in existing:                                   # 기존 financials.json 회사
        c = r.get("corp_code")
        if c and c not in uni:
            uni[c] = {"name": r.get("corp_name", ""), "stock": r.get("stock", "")}
    n_cur = len(uni)
    # 역사 완전 백필: 요청 연도 시기의 정기공시 제출자(상장폐지사 포함) 합집합.
    span_bgn = date(min(args.years), 1, 1)
    span_end = min(today_d, date(max(args.years) + 1, 6, 30))  # 사업보고서는 익년 제출 → +반년
    for code, v in ensure_universe(span_bgn, span_end).items():
        uni.setdefault(code, {"name": v["name"], "stock": v["stock"]})
    codes = sorted(uni)
    if args.limit:
        codes = codes[:args.limit]
    n_batches = (len(codes) + config.MULTI_BATCH - 1) // config.MULTI_BATCH
    print(f"유니버스 {len(codes):,}개사 (기존 {n_cur:,} + 제출자열거 추가 {len(uni)-n_cur:,}) · "
          f"{n_batches}배치 · 백필 연도 {args.years}", flush=True)

    index = {(r["corp_code"], r["year"], r["reprt"]): r for r in existing}
    today = datetime.now(KST).strftime("%Y-%m-%d")
    calls = 0
    t0 = time.time()

    for year in args.years:
        ystr = str(year)
        do_idx = year >= 2023                       # 재무지표는 2023 3분기~만
        added = 0
        for reprt in config.REPRT_ALL:
            period = {}
            for batch in chunked(codes, config.MULTI_BATCH):
                try:
                    rows = dart_api.get_multi_account(batch, ystr, reprt)
                except Exception:
                    rows = []
                calls += 1
                for r in rows:
                    corp, fs, nm = r.get("corp_code"), r.get("fs_div"), r.get("account_nm")
                    if not corp or fs not in ("CFS", "OFS") or not nm:
                        continue
                    rec = period.setdefault(corp, _blank(corp, uni, ystr, reprt, today))
                    rec["acct"][fs].setdefault(nm, parse_amount(r.get("thstrm_amount")))
                    if not rec["rcept_no"]:
                        rec["rcept_no"] = r.get("rcept_no", "")
                    cur = (r.get("currency") or "").strip()
                    if (len(cur) == 3 and cur.isascii() and cur.isalpha()
                            and cur.upper() != "KRW" and (fs == "CFS" or rec["currency"] == "KRW")):
                        rec["currency"] = cur.upper()
                if do_idx:
                    for cat, code in config.IDX_CL_CODE.items():
                        try:
                            irows = dart_api.get_company_index(batch, ystr, reprt, code)
                        except Exception:
                            irows = []
                        calls += 1
                        for r in irows:
                            corp, inm, val = r.get("corp_code"), r.get("idx_nm"), r.get("idx_val")
                            if not corp or not inm or not (val or "").strip():
                                continue
                            rec = period.setdefault(corp, _blank(corp, uni, ystr, reprt, today))
                            sd = r.get("stlm_dt", "")
                            if sd:
                                rec["stlm_dt"] = sd
                            rec["idx"].setdefault(cat, {})[inm] = val
                time.sleep(config.REQUEST_SLEEP)
            for corp, rec in period.items():
                if rec["acct"]["CFS"] or rec["acct"]["OFS"] or rec["idx"]:
                    index[(corp, ystr, reprt)] = rec
                    added += 1
            print(f"  {year} {config.REPRT_NM[reprt]} 완료 (누적 {calls}콜)", flush=True)
        save_fin(list(index.values()))              # 연도 체크포인트
        print(f"  → {year} 병합 {added}건 저장 (financials.json 총 {len(index):,})", flush=True)

    print(f"\n완료: {calls}콜 · {time.time()-t0:.0f}초 · financials.json {len(index):,}레코드", flush=True)


if __name__ == "__main__":
    main()
