"""DS001 기업개황(company.json) 수집기 — 회사 프로필 (기간 무관, 일회성).

대상 유니버스: enum_{year}.json 의 B∪C∪D 제출 회사 합집합 (= 그 해 활동 회사).
필요 시 corp_codes 인자로 외부 목록(예: FilingHub filers)도 합칠 수 있음.

수집 필드(원본 그대로): corp_name(_eng), stock_name/code, ceo_nm, corp_cls,
  jurir_no(법인등록번호), bizr_no(사업자번호), adres, hm_url, phn_no, fax_no,
  induty_code(업종), est_dt(설립일), acc_mt(결산월).

실행:
  python auto/company_collect.py                 # enum_{올해}.json 유니버스
  python auto/company_collect.py 2026            # 특정 연도 enum 파일
"""
from __future__ import annotations
import sys
import json
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

KST = timezone(timedelta(hours=9))


def universe_from_enum(year: int) -> dict:
    """enum_{year}.json 에서 B∪C∪D 회사 코드→메타 합집합."""
    path = config.OUTPUT_DIR / f"enum_{year}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path.name} 없음 — enumerate_filings.py 먼저 실행")
    d = json.loads(path.read_text(encoding="utf-8"))
    uni: dict = {}
    for info in d["types"].values():
        for c in info["corp_list"]:
            uni.setdefault(c["code"], c)
    return uni


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(KST).year
    uni = universe_from_enum(year)
    codes = sorted(uni)
    print(f"기업개황 대상 {len(codes):,}개사 (enum_{year} 합집합)", flush=True)

    today = datetime.now(KST).strftime("%Y-%m-%d")
    out = []
    calls = 0
    empty = 0
    for i, code in enumerate(codes, 1):
        try:
            prof = dart_api.get_company(code)
        except dart_api.DartApiError as e:
            print(f"  ⚠️ {code}: {e}", flush=True)
            prof = {}
        calls += 1
        if prof:
            prof["_fetched"] = today
            out.append(prof)
        else:
            empty += 1
        time.sleep(config.REQUEST_SLEEP)
        if i % 300 == 0:
            print(f"  {i}/{len(codes)} (콜 {calls}, 수집 {len(out)})", flush=True)

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    path = config.OUTPUT_DIR / "companies.json"
    path.write_text(json.dumps({"fetched": today, "count": len(out), "companies": out},
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n완료: {len(out):,}개사 · {calls}콜 · 빈응답 {empty} → {path.name} "
          f"({path.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
