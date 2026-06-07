"""레지스트리의 모든 엔드포인트가 실제 존재·동작하는지 라이브 검증.

테스트 회사(기본 삼성전자)로 각 엔드포인트를 1회 호출 → status 가 000(데이터) 또는
013(데이터없음)이면 '엔드포인트 유효', 그 외(100/101/404 등)면 '엔드포인트/파라미터 오류'.
틀린 엔드포인트는 조용히 실패하므로 본수집 전 반드시 통과시킨다.

실행:  python auto/validate_endpoints.py [corp_code]
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config
import dart_api
import registry

TEST_CORP = sys.argv[1] if len(sys.argv) > 1 else "00126380"  # 삼성전자
BGN, END = "20150101", "20260606"


def check(endpoint: str, needs_range: bool):
    url = f"{config.API_BASE}/{endpoint}.json"
    params = {"corp_code": TEST_CORP}
    if needs_range:
        params.update(bgn_de=BGN, end_de=END)
    try:
        data = dart_api._get(url, params)
        st = data.get("status")
        n = len(data.get("list", []))
        return ("OK" if st == "000" else "EMPTY", f"status={st} rows={n}")
    except dart_api.DartApiError as e:
        return ("FAIL", str(e))


def main():
    print(f"엔드포인트 검증 (테스트회사 {TEST_CORP}, {BGN}~{END})\n")
    fails = []
    for gname, table, needs_range in (
            ("DS004", registry.DS004, False),
            ("DS005", registry.DS005, True),
            ("DS006", registry.DS006, True)):
        print(f"── {gname} ({len(table)}개) ──")
        for endpoint, label, key in table:
            verdict, detail = check(endpoint, needs_range)
            mark = {"OK": "✅", "EMPTY": "·", "FAIL": "❌"}[verdict]
            print(f"  {mark} {endpoint:<24} {label:<22} {detail}")
            if verdict == "FAIL":
                fails.append((gname, endpoint, label, detail))
        print()
    if fails:
        print(f"⚠️ 실패 {len(fails)}개 — 엔드포인트명 재확인 필요:")
        for g, e, l, d in fails:
            print(f"   [{g}] {e} ({l}) → {d}")
    else:
        print("🎉 전 엔드포인트 유효 (000/013).")


if __name__ == "__main__":
    main()
