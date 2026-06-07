"""공시검색(list.json) 구동축 — 기간 동안 주요사항(B)·발행(C)·지분(D) 제출 열거.

corp_code 미지정 검색은 3개월 제약 → 89일 청킹. 공식 API라 차단 없음.
산출(output/enum_{year}.json): 유형별 제출 회사·보고서명(report_nm) 빈도 + 회사목록.
용도: ① DS004/005/006 실제 대상 규모 확인  ② report_nm → API 매칭 근거.

실행:
  python auto/enumerate_filings.py                 # 올해 1/1 ~ 오늘
  python auto/enumerate_filings.py 20260101 20260606
"""
from __future__ import annotations
import sys
import json
import time
from datetime import date, datetime, timedelta, timezone
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config
import dart_api

KST = timezone(timedelta(hours=9))


def _chunks(start: date, end: date, days: int = 89):
    cur = start
    while cur <= end:
        ce = min(cur + timedelta(days=days), end)
        yield cur, ce
        if ce >= end:
            break
        cur = ce + timedelta(days=1)


def enum_type(ty: str, bgn: date, end: date):
    """한 공시유형을 기간 청킹 + 페이지네이션으로 전부 훑는다."""
    corps: dict[str, dict] = {}      # corp_code -> {name, cls, stock}
    rpt = Counter()                  # report_nm 빈도
    pairs: set = set()               # (corp_code, report_nm) — 스마트 호출 추정
    filings = 0
    calls = 0
    for cs, ce in _chunks(bgn, end):
        page = 1
        while True:
            data = dart_api.list_disclosures(
                cs.strftime("%Y%m%d"), ce.strftime("%Y%m%d"),
                pblntf_ty=ty, page_no=page, page_count=config.PAGE_COUNT)
            calls += 1
            if data.get("status") == "013":
                break
            for it in data.get("list", []):
                code = it.get("corp_code")
                if not code:
                    continue
                nm = (it.get("report_nm") or "").strip()
                corps[code] = {
                    "name": it.get("corp_name", ""),
                    "cls": it.get("corp_cls", ""),
                    "stock": (it.get("stock_code") or "").strip(),
                }
                rpt[nm] += 1
                pairs.add((code, nm))
                filings += 1
            tp = int(data.get("total_page", 1) or 1)
            if page >= tp:
                break
            page += 1
            time.sleep(config.REQUEST_SLEEP)
        time.sleep(config.REQUEST_SLEEP)
    return corps, rpt, pairs, filings, calls


def main():
    args = sys.argv[1:]
    today = datetime.now(KST).date()
    bgn = (datetime.strptime(args[0], "%Y%m%d").date()
           if len(args) > 0 else date(today.year, 1, 1))
    end = (datetime.strptime(args[1], "%Y%m%d").date()
           if len(args) > 1 else today)
    print(f"공시검색 열거: {bgn} ~ {end}  (89일 청킹)")

    out = {"range": [bgn.isoformat(), end.isoformat()], "types": {}}
    total_calls = 0
    for ty, label in config.PBLNTF_TY.items():
        corps, rpt, pairs, filings, calls = enum_type(ty, bgn, end)
        total_calls += calls
        out["types"][ty] = {
            "label": label,
            "corps": len(corps),
            "filings": filings,
            "distinct_corp_report": len(pairs),   # 스마트 매칭 호출 추정치
            "enum_calls": calls,
            "by_cls": dict(Counter(c["cls"] for c in corps.values())),
            "top_reports": rpt.most_common(50),
            "corp_list": [{"code": k, **v} for k, v in corps.items()],
        }
        print(f"  [{ty} {label}] 회사 {len(corps):,} · 공시 {filings:,} · "
              f"고유(회사×보고서) {len(pairs):,} · 열거 {calls}콜", flush=True)

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    path = config.OUTPUT_DIR / f"enum_{end.year}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")

    d = out["types"]
    est = {k: d.get(k, {}).get("distinct_corp_report", 0) for k in ("B", "C", "D")}
    print(f"\n열거 총 {total_calls}콜 → {path.name}")
    print("── 예상 데이터 콜 (스마트 매칭) ──")
    print(f"  DS004 지분(D)     ~{est['D']:,}")
    print(f"  DS005 주요사항(B) ~{est['B']:,}")
    print(f"  DS006 증권신고(C) ~{est['C']:,}")
    print(f"  소계              ~{sum(est.values()):,}  (+ 열거 {total_calls} + 기업개황 별도)")


if __name__ == "__main__":
    main()
