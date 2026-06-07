"""DS004/005/006 report_nm → OpenDART 엔드포인트 매핑 레지스트리.

각 항목: (endpoint, label, match_key). report_nm 안에 match_key 문자열이 포함되면
그 API 로 매핑한다. [기재정정]/[첨부정정] 등 접두사·래퍼는 substring 매칭이라 자동 흡수.
구조화 API 없는 유형(파생결합·실적보고서·의결권대리행사·투자계약증권 등)은
레지스트리에 없으므로 매칭 실패 → 자동 skip (= 2층 데이터 없음).

⚠️ 순서 중요: 더 구체적인 키를 먼저 둔다(첫 매칭 채택). 예) '유무상증자' > '무상증자',
   '해외…상장폐지결정' > '상장결정' > '상장폐지' > '상장', '회사분할합병' > '회사분할'.
엔드포인트명은 validate_endpoints.py 로 라이브 검증 후 확정.
"""

# DS004 지분공시 — corp_code 만 받음 (날짜범위 파라미터 없음).
DS004 = [
    ("elestock",   "임원·주요주주 소유보고", "임원ㆍ주요주주특정증권등소유"),
    ("majorstock", "대량보유 상황보고",       "대량보유상황보고"),
]

# DS005 주요사항보고서 — corp_code + bgn_de + end_de. (구체적 키 우선 정렬)
DS005 = [
    ("tsstkAqTrctrCnsDecsn", "자기주식취득 신탁계약 체결 결정", "자기주식취득신탁계약체결결정"),
    ("tsstkAqTrctrCcDecsn",  "자기주식취득 신탁계약 해지 결정", "자기주식취득신탁계약해지결정"),
    ("tsstkAqDecsn",         "자기주식 취득 결정",             "자기주식취득결정"),
    ("tsstkDpDecsn",         "자기주식 처분 결정",             "자기주식처분결정"),
    ("pifricDecsn",          "유무상증자 결정",                "유무상증자결정"),
    ("piicDecsn",            "유상증자 결정",                  "유상증자결정"),
    ("fricDecsn",            "무상증자 결정",                  "무상증자결정"),
    ("crDecsn",              "감자 결정",                      "감자결정"),
    ("cvbdIsDecsn",          "전환사채권 발행결정",            "전환사채권발행결정"),
    ("bdwtIsDecsn",          "신주인수권부사채권 발행결정",    "신주인수권부사채권발행결정"),
    ("exbdIsDecsn",          "교환사채권 발행결정",            "교환사채권발행결정"),
    ("wdCocobdIsDecsn",      "상각형 조건부자본증권 발행결정", "상각형조건부자본증권발행결정"),
    ("cmpDvmgDecsn",         "회사분할합병 결정",              "회사분할합병결정"),
    ("cmpDvDecsn",           "회사분할 결정",                  "회사분할결정"),
    ("cmpMgDecsn",           "회사합병 결정",                  "회사합병결정"),
    ("stkExtrDecsn",         "주식교환·이전 결정",             "주식교환"),
    ("otcprStkInvscrTrfDecsn", "타법인주식·출자증권 양도결정", "타법인주식및출자증권양도결정"),
    ("otcprStkInvscrInhDecsn", "타법인주식·출자증권 양수결정", "타법인주식및출자증권양수결정"),
    ("tgastTrfDecsn",        "유형자산 양도 결정",             "유형자산양도결정"),
    ("tgastInhDecsn",        "유형자산 양수 결정",             "유형자산양수결정"),
    ("bsnTrfDecsn",          "영업양도 결정",                  "영업양도결정"),
    ("bsnInhDecsn",          "영업양수 결정",                  "영업양수결정"),
    ("astInhtrfEtcPtbkOpt",  "자산양수도(기타)·풋백옵션",      "풋백옵션"),
    ("lwstLg",               "소송 등의 제기",                 "소송등의제기"),
    ("dsRsOcr",              "해산사유 발생",                  "해산사유발생"),
    ("ctrcvsBgrq",           "회생절차 개시신청",              "회생절차개시신청"),
    ("dfOcr",                "부도발생",                       "부도발생"),
    ("bsnSp",                "영업정지",                       "영업정지"),
    ("bnkMngtPcbg",          "채권은행 등의 관리절차 개시",    "관리절차개시"),
    ("bnkMngtPcsp",          "채권은행 등의 관리절차 중단",    "관리절차중단"),
    ("ovDlstDecsn",          "해외상장폐지 결정",              "해외증권시장주권등상장폐지결정"),
    ("ovLstDecsn",           "해외상장 결정",                  "해외증권시장주권등상장결정"),
    ("ovDlst",               "해외상장폐지",                   "해외증권시장주권등상장폐지"),
    ("ovLst",                "해외상장",                       "해외증권시장주권등상장"),
    ("stkrtbdInhDecsn",      "주권 관련 사채권 양수 결정",     "주권관련사채권양수결정"),
    ("stkrtbdTrfDecsn",      "주권 관련 사채권 양도 결정",     "주권관련사채권양도결정"),
]

# DS006 증권신고서 — corp_code + bgn_de + end_de.
DS006 = [
    ("estkRs",  "지분증권",                "증권신고서(지분증권)"),
    ("bdRs",    "채무증권",                "증권신고서(채무증권)"),
    ("stkdpRs", "증권예탁증권",            "증권신고서(증권예탁증권)"),
    ("mgRs",    "합병",                    "증권신고서(합병)"),
    ("dvRs",    "분할",                    "증권신고서(분할)"),
    ("extrRs",  "주식의포괄적교환·이전",   "증권신고서(주식의포괄적교환"),
]


def _match(table, report_nm: str):
    for endpoint, label, key in table:
        if key in report_nm:
            return endpoint
    return None


def match_ds004(report_nm): return _match(DS004, report_nm)
def match_ds005(report_nm): return _match(DS005, report_nm)
def match_ds006(report_nm): return _match(DS006, report_nm)


# pblntf_ty → (matcher, table, 날짜범위 필요?)
GROUP = {
    "B": ("DS005", match_ds005, DS005, True),
    "C": ("DS006", match_ds006, DS006, True),
    "D": ("DS004", match_ds004, DS004, False),
}
