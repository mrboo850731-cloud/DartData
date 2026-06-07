"""OpenDART API 클라이언트 (DartData) — 공시검색/기업개황/이벤트성 수집.

FilingHub auto/dart_api.py 의 _get 패턴을 그대로 따른다:
재시도·백오프, status 검사, 013(데이터없음)=빈 결과. 재무(DS003)는 여기서 안 다룬다.
"""
from __future__ import annotations
import time
import requests
import config

_STATUS_MSG = {
    "000": "정상",
    "010": "등록되지 않은 인증키",
    "011": "사용할 수 없는 인증키(비활성/만료)",
    "013": "조회된 데이터 없음",
    "020": "요청 제한 초과(일일 한도)",
    "100": "필드의 부적절한 값",
    "101": "부적절한 접근",
    "800": "시스템 점검 중",
    "900": "정의되지 않은 오류",
}


class DartApiError(RuntimeError):
    """DART API 가 status != 000 (013 제외) 을 반환했을 때."""


def _get(url: str, params: dict) -> dict:
    """공통 GET — 인증키 주입 + status 검사. 013 은 빈 list 로 정상 처리."""
    if not config.DART_API_KEY:
        raise DartApiError("DART_API_KEY 미설정 — auto/.env 확인")
    q = {"crtfc_key": config.DART_API_KEY, **params}
    last = None
    for attempt in range(4):                 # 최대 4회 (재시도 3) — 긴 수집 견고화
        try:
            r = requests.get(url, params=q, timeout=30)
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status == "013":
                return {"status": "013", "list": []}
            if status != "000":
                msg = _STATUS_MSG.get(status, "알 수 없음")
                raise DartApiError(
                    f"DART status={status} ({msg}): {data.get('message', '')}")
            return data
        except DartApiError:
            raise                            # DART 논리 오류는 재시도 안 함
        except (requests.exceptions.RequestException, ValueError) as e:
            # ValueError = JSON 파싱 실패(일시적 비-JSON 응답: 점검페이지/HTML 등) → 재시도
            last = e
            if attempt < 3:
                time.sleep(2 ** attempt)     # 1·2·4초 백오프
                continue
            raise DartApiError(f"요청 오류 {attempt + 1}회 실패: {e}")
    raise DartApiError(f"요청 오류: {last}")


def list_disclosures(bgn_de: str, end_de: str, pblntf_ty: str | None = None,
                     page_no: int = 1, page_count: int = 100) -> dict:
    """공시검색(list.json) 한 페이지. corp_code 미지정 시 기간 3개월 제약 → 89일 청킹.

    각 항목: corp_code, corp_name, stock_code, corp_cls(Y/K/N/E),
             report_nm, rcept_no, rcept_dt, flr_nm.
    """
    params = {"bgn_de": bgn_de, "end_de": end_de,
              "page_no": page_no, "page_count": page_count}
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty
    return _get(config.EP_LIST, params)


def get_company(corp_code: str) -> dict:
    """기업개황(company.json) — 회사 프로필(대표자/주소/업종/설립일/결산월 등).

    응답 자체가 프로필 필드(list 아님). 013 이면 빈 dict 반환.
    """
    data = _get(config.EP_COMPANY, {"corp_code": corp_code})
    if data.get("status") == "013":
        return {}
    return data


def get_multi_account(corp_codes, bsns_year: str, reprt_code: str) -> list[dict]:
    """다중회사 주요계정(fnlttMultiAcnt) — 최대 100개사 한 번에. 각 행에 corp_code 포함.

    한 콜로 연결(CFS)+별도(OFS)·BS+IS 가 모두 온다. (DS003, FilingHub 이식)
    """
    codes = ",".join(corp_codes) if isinstance(corp_codes, (list, tuple)) else corp_codes
    data = _get(config.EP_MULTI_ACCOUNT, {
        "corp_code": codes, "bsns_year": bsns_year, "reprt_code": reprt_code})
    return data.get("list", [])


def get_company_index(corp_codes, bsns_year: str, reprt_code: str,
                      idx_cl_code: str) -> list[dict]:
    """다중회사 주요 재무지표(fnlttCmpnyIndx) — 최대 100개사. idx_cl_code 필수.

    2023 3분기 이후 데이터만 존재. 각 행에 corp_code·stlm_dt·idx_nm·idx_val 포함.
    """
    codes = ",".join(corp_codes) if isinstance(corp_codes, (list, tuple)) else corp_codes
    data = _get(config.EP_COMPANY_INDEX, {
        "corp_code": codes, "bsns_year": bsns_year, "reprt_code": reprt_code,
        "idx_cl_code": idx_cl_code})
    return data.get("list", [])
