"""DS004/005/006 스마트 수집기 — 공시검색 구동 + report_nm→엔드포인트 매칭.

흐름:
  1) list.json 으로 기간 내 B(주요사항)·C(발행)·D(지분) 제출 열거 (corp, report_nm).
  2) registry 로 report_nm → 엔드포인트 매칭. 구조화 API 없는 유형은 자동 skip.
  3) (corp, endpoint) 중복 제거 → 각 1회 호출
     (DS005/006: corp+bgn+end · DS004: corp_code 만).
  4) 응답 raw list 를 랜딩 레코드로 로컬 JSON 저장 (외화 등 원본 필드 무손실 보존
     → 대안 B 통화원칙은 소비단에서 적용).

실행:
  python auto/collect.py                      # 올해 1/1 ~ 오늘
  python auto/collect.py 20260101 20260606
  python auto/collect.py --limit 30           # 처음 30콜만 (검증용)
"""
from __future__ import annotations
import sys
import json
import time
import argparse
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
import registry
import enumerate_filings as enm

KST = timezone(timedelta(hours=9))


def build_worklist(bgn: date, end: date, types=("B", "C", "D")):
    """열거 → (작업항목 dict, corp 메타, skip 카운터, 열거 콜수).

    작업항목 키 = (corp_code, endpoint) — 중복 제거. 날짜범위 API는 1콜이 기간 전체 커버.
    types: 수집 공시유형. 백필 시 ("B","C")로 DS004(D) 제외(연도무관이라 1회면 충분).
    """
    work: dict = {}
    meta: dict = {}
    skipped = Counter()
    enum_calls = 0
    for ty in types:
        corps, rpt, pairs, filings, calls = enm.enum_type(ty, bgn, end)
        enum_calls += calls
        meta.update(corps)
        gname, matcher, table, needs_range = registry.GROUP[ty]
        for corp, report_nm in pairs:
            ep = matcher(report_nm)
            if not ep:
                skipped[report_nm] += 1
                continue
            key = (corp, ep)
            if key not in work:
                label = next((l for (e, l, k) in table if e == ep), ep)
                work[key] = {"group": gname, "endpoint": ep, "label": label,
                             "corp_code": corp, "needs_range": needs_range}
    return work, meta, skipped, enum_calls


def _dump(out_path, range_, today, records):
    """현재까지 수집한 레코드를 원자적으로 저장 (체크포인트/최종 공용)."""
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps({"range": range_, "fetched": today, "records": records},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    import os
    os.replace(tmp, out_path)


def collect(bgn: date, end: date, limit=None, out_path=None, types=("B", "C", "D")):
    bgn_s, end_s = bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    print(f"열거 중 {bgn} ~ {end} (유형 {','.join(types)}) …", flush=True)
    work, meta, skipped, enum_calls = build_worklist(bgn, end, types)
    items = list(work.values())
    if limit:
        items = items[:limit]
    print(f"열거 {enum_calls}콜 · 작업 {len(items):,}건 · "
          f"skip {sum(skipped.values()):,}건({len(skipped)}종)", flush=True)

    today = datetime.now(KST).strftime("%Y-%m-%d")
    records = []
    done = set()
    if out_path is not None and out_path.exists():            # 같은 범위면 이어받기
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            if prev.get("range") == [bgn_s, end_s]:
                records = prev.get("records", [])
                done = {(r.get("endpoint"), r.get("corp_code")) for r in records}
                print(f"  재개: 기존 {len(records):,}레코드 로드 → 이어서 수집", flush=True)
        except Exception:
            pass
    calls = 0
    by_group = Counter(r["group"] for r in records)
    errors = []
    for i, it in enumerate(items, 1):
        ep, corp = it["endpoint"], it["corp_code"]
        if (ep, corp) in done:                                 # 이미 수집된 키 skip
            continue
        url = f"{config.API_BASE}/{ep}.json"
        params = {"corp_code": corp}
        if it["needs_range"]:
            params.update(bgn_de=bgn_s, end_de=end_s)
        data = None
        try:
            data = dart_api._get(url, params)
        except Exception as e:                 # catch-all: 한 건 오류가 전체 수집을 죽이지 않음
            errors.append({"group": it["group"], "endpoint": ep,
                           "corp": corp, "error": str(e)})
        calls += 1
        if data is not None:
            # OpenDART 응답은 단일 list 또는 다중 group([{title,list}]) 두 포맷.
            # (증권신고서 등은 group 구조 → list 만 보면 빈 것처럼 보임)
            rows = data.get("list") or []
            groups = data.get("group") or []
            n = len(rows) + sum(len(g.get("list") or []) for g in groups)
            if n:
                m = meta.get(corp, {})
                rec = {
                    "group": it["group"], "endpoint": ep, "label": it["label"],
                    "corp_code": corp, "corp_name": m.get("name", ""),
                    "stock": m.get("stock", ""), "cls": m.get("cls", ""),
                    "params": params, "n": n, "fetched": today,
                }
                if rows:
                    rec["rows"] = rows
                if groups:
                    rec["groups"] = groups
                records.append(rec)
                by_group[it["group"]] += 1
        time.sleep(config.REQUEST_SLEEP)
        if i % 200 == 0:
            print(f"  {i}/{len(items)} (누적 {calls}콜, 데이터 {len(records)})", flush=True)
        if out_path is not None and i % 300 == 0:
            _dump(out_path, [bgn_s, end_s], today, records)   # 체크포인트 (죽어도 보존)

    return {
        "records": records, "calls": calls, "enum_calls": enum_calls,
        "skipped": skipped, "errors": errors, "by_group": by_group,
        "range": [bgn_s, end_s], "items": len(items),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bgn", nargs="?", help="시작일 YYYYMMDD")
    ap.add_argument("end", nargs="?", help="종료일 YYYYMMDD")
    ap.add_argument("--limit", type=int, default=None, help="처음 N콜만 (검증용)")
    ap.add_argument("--no-ds004", action="store_true",
                    help="DS004(지분공시) 제외 — 과거연도 백필용(DS004는 연도무관, 1회면 충분)")
    args = ap.parse_args()

    today = datetime.now(KST).date()
    bgn = datetime.strptime(args.bgn, "%Y%m%d").date() if args.bgn else date(today.year, 1, 1)
    end = datetime.strptime(args.end, "%Y%m%d").date() if args.end else today

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    suffix = "_sample" if args.limit else ""
    out_path = config.OUTPUT_DIR / f"events_{end.year}{suffix}.json"

    types = ("B", "C") if args.no_ds004 else ("B", "C", "D")
    t0 = time.time()
    r = collect(bgn, end, limit=args.limit, out_path=out_path, types=types)

    _dump(out_path, r["range"], datetime.now(KST).strftime("%Y-%m-%d"), r["records"])  # 최종 저장
    # skip 유형 상위 + 에러 기록.
    diag = {
        "by_group": dict(r["by_group"]),
        "skipped_top": Counter(r["skipped"]).most_common(40),
        "errors": r["errors"],
    }
    (config.OUTPUT_DIR / f"events_{end.year}{suffix}_diag.json").write_text(
        json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")

    total_rows = sum(rec["n"] for rec in r["records"])
    print(f"\n완료: 작업 {r['items']:,} · 데이터콜 {r['calls']:,} · 열거 {r['enum_calls']} · "
          f"{time.time()-t0:.0f}초")
    print(f"  레코드(데이터보유) {len(r['records']):,} · 총 raw행 {total_rows:,}")
    print(f"  그룹별: {dict(r['by_group'])}")
    if r["errors"]:
        print(f"  ⚠️ 에러 {len(r['errors'])}건 → diag 파일 참조")
    print(f"  → {out_path.name} ({out_path.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
