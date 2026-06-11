"""DartData 환경 설정 — OpenDART 공시/이벤트/지분/발행 수집 (DS001·004·005·006).

설계 메모:
  - DS003 재무는 FilingHub financials.json 복사로 충당 → 여기서 수집 안 함.
  - DS002 정기보고서 주요정보는 Phase 2(별도)로 보류.
  - 인증키는 FilingHub와 동일(1인 1키, 약관 준수) — auto/.env 의 DART_API_KEY.
  - 수집 전략: 공시검색(list.json)을 구동축으로 '실제 제출된 것만' 매칭 호출(헛콜 ~0).
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

DART_API_KEY = os.getenv("DART_API_KEY", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "").strip()   # healthchecks.io ping (서버 .env에만)

OUTPUT_DIR = ROOT / "output"

# ── OpenDART 엔드포인트 ──────────────────────────────────────────────
API_BASE = "https://opendart.fss.or.kr/api"
EP_LIST = f"{API_BASE}/list.json"          # 공시검색 (구동축)
EP_COMPANY = f"{API_BASE}/company.json"     # 기업개황 (DS001)
EP_CORP_CODE = f"{API_BASE}/corpCode.xml"   # 고유번호 ZIP (시드)

# DS004 지분공시 종합정보 (각 보고서종류 → 전용 엔드포인트).
EP_ELESTOCK = f"{API_BASE}/elestock.json"     # 임원·주요주주 소유보고
EP_MAJORSTOCK = f"{API_BASE}/majorstock.json"  # 대량보유 상황보고

# DS003 재무정보 (다중회사 API — 100개 묶음 배치). FilingHub에서 이식.
EP_MULTI_ACCOUNT = f"{API_BASE}/fnlttMultiAcnt.json"   # 다중회사 주요계정
EP_COMPANY_INDEX = f"{API_BASE}/fnlttCmpnyIndx.json"   # 다중회사 주요 재무지표
EP_SINGLE_ACCOUNT_ALL = f"{API_BASE}/fnlttSinglAcntAll.json"  # 단일회사 전체재무제표(전 계정)
FS_DIV = ["CFS", "OFS"]                                # 연결·별도 (전체재무제표는 단건 호출)
MULTI_BATCH = 100                                       # 다중회사 1콜 최대 회사 수
REPRT_NM = {"11011": "사업보고서", "11012": "반기보고서",
            "11013": "1분기보고서", "11014": "3분기보고서"}
REPRT_ALL = ["11013", "11012", "11014", "11011"]       # 1분기·반기·3분기·사업
REPRT_STLM = {"11013": "03-31", "11012": "06-30", "11014": "09-30", "11011": "12-31"}
IDX_CL_CODE = {"수익성": "M210000", "안정성": "M220000",
               "성장성": "M230000", "활동성": "M240000"}  # 재무지표는 2023 3분기~만 존재

# DS002 정기보고서 주요정보 — 30개 단일회사 API (전부 corp_code+bsns_year+reprt_code).
# 응답 28개 평면 list, 2개(V2 보수)만 다중그룹. V2는 최신연도용(과거는 v1). 라이브검증 완료(2026-06-11).
DS002_ENDPOINTS = [
    ("stockTotqySttus", "주식의총수현황"), ("tesstkAcqsDspsSttus", "자기주식취득처분"),
    ("alotMatter", "배당"), ("irdsSttus", "증자감자현황"),
    ("detScritsIsuAcmslt", "채무증권발행실적"), ("entrprsBilScritsNrdmpBlce", "기업어음미상환잔액"),
    ("srtpdPsndbtNrdmpBlce", "단기사채미상환잔액"), ("cprndNrdmpBlce", "회사채미상환잔액"),
    ("newCaplScritsNrdmpBlce", "신종자본증권미상환잔액"), ("cndlCaplScritsNrdmpBlce", "조건부자본증권미상환잔액"),
    ("pssrpCptalUseDtls", "공모자금사용내역"), ("prvsrpCptalUseDtls", "사모자금사용내역"),
    ("accnutAdtorNmNdAdtOpinion", "회계감사인명칭및감사의견"), ("adtServcCnclsSttus", "감사용역체결현황"),
    ("accnutAdtorNonAdtServcCnclsSttus", "비감사용역계약현황"), ("outcmpnyDrctrNdChangeSttus", "사외이사및변동현황"),
    ("hyslrSttus", "최대주주현황"), ("hyslrChgSttus", "최대주주변동현황"),
    ("mrhlSttus", "소액주주현황"), ("exctvSttus", "임원현황"), ("empSttus", "직원현황"),
    ("unrstExctvMendngSttus", "미등기임원보수현황"),
    ("drctrAdtAllMendngSttusGmtsckConfmAmount", "이사감사보수_주총승인금액"),
    ("hmvAuditAllSttus", "이사감사보수_지급금액전체"),
    ("drctrAdtAllMendngSttusMendngPymntamtTyCl", "이사감사보수_지급금액유형별"),
    ("hmvAuditIndvdlBySttus", "개인별보수5억이상"), ("hmvAuditIndvdlBySttusV2", "개인별보수5억이상_V2"),
    ("indvdlByPay", "개인별보수상위5인"), ("indvdlByPayV2", "개인별보수상위5인_V2"),
    ("otrCprInvstmntSttus", "타법인출자현황"),
]

# 공시유형(pblntf_ty) — 구동축으로 쓰는 3종.
PBLNTF_TY = {
    "B": "주요사항보고",   # → DS005 (발행결정·M&A·부도·소송 등 36개)
    "C": "발행공시",       # → DS006 (증권신고서: 지분/채무/합병/분할/교환이전/예탁 6개)
    "D": "지분공시",       # → DS004 (임원·주요주주 소유, 대량보유 2개)
}

REQUEST_SLEEP = 0.12   # 목록 API 페이스 (가벼움, 공식 API라 차단 없음)
PAGE_COUNT = 100       # list.json 페이지당 최대
