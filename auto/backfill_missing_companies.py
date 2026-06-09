"""회사개황 누락 보완 — financials는 있으나 companies(회사개황)가 없는 회사를 보완.

배경: company_collect 유니버스는 enum(B/C/D 이벤트) 합집합이라, 정기보고서/financials만
있고 최근 B/C/D 이벤트가 없는 회사는 회사개황(=종목명)이 빠진다. Disclo가 보여줄 수 있는
회사는 반드시 financials가 있으므로, financials 기준으로 reconcile하면 종목명 표시가 보장된다.

멱등(on_conflict=corp_code). corpCode로 직접 회사개황(DS001) fetch → companies 업서트.

실행:
  python auto/backfill_missing_companies.py                 # reconcile: financials−companies 자동 백필
  python auto/backfill_missing_companies.py 01259418 ...    # corp_code 직접 지정
"""
from __future__ import annotations
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config
import dart_api
import supabase_client as sb

KST = timezone(timedelta(hours=9))


def _reconcile_targets() -> list:
    """financials에 있으나 companies에 없는 corp_code(= 회사개황 누락)."""
    print("reconcile: companies·financials corp_code 대조 중…", flush=True)
    comp = {c["corp_code"] for c in sb.get_all("companies?select=corp_code")}
    fin = {r["corp_code"] for r in sb.get_all("financials?select=corp_code")}
    gap = sorted(fin - comp)
    print(f"  companies {len(comp):,} · financials(고유) {len(fin):,} · 누락 {len(gap)}", flush=True)
    return gap


def main():
    args = sys.argv[1:]
    codes = args if args else _reconcile_targets()
    if not codes:
        print("백필 대상 없음 — companies가 financials를 모두 포함(완전).")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    rows, fail = [], []
    for code in codes:
        try:
            prof = dart_api.get_company(code)
        except dart_api.DartApiError as e:
            print(f"  ⚠️ {code}: {e}", flush=True)
            fail.append(code); time.sleep(config.REQUEST_SLEEP); continue
        if not prof:
            print(f"  ⚠️ {code}: 빈 응답(013)", flush=True)
            fail.append(code); time.sleep(config.REQUEST_SLEEP); continue
        prof["_fetched"] = today
        prof.setdefault("corp_code", code)
        rows.append({
            "corp_code": prof.get("corp_code") or code,
            "corp_name": prof.get("corp_name"),
            "corp_name_eng": prof.get("corp_name_eng"),
            "stock_code": prof.get("stock_code"),
            "ceo_nm": prof.get("ceo_nm"),
            "corp_cls": prof.get("corp_cls"),
            "induty_code": prof.get("induty_code"),
            "est_dt": prof.get("est_dt"),
            "acc_mt": prof.get("acc_mt"),
            "adres": prof.get("adres"),
            "profile": prof,
        })
        print(f"  ✓ {code}  {prof.get('corp_name')}  종목명={prof.get('stock_name')}  "
              f"stock_code={prof.get('stock_code')}", flush=True)
        time.sleep(config.REQUEST_SLEEP)

    if rows:
        sb.upsert("companies", rows, "corp_code")
        print(f"\n업서트 완료: {len(rows)}개사 (실패 {len(fail)})")
    else:
        print("\n업서트할 행 없음")
    if fail:
        print("실패 코드:", fail)


if __name__ == "__main__":
    main()
