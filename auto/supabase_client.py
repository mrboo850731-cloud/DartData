"""Supabase REST(PostgREST) 업서트 헬퍼 — service_role 키 사용(서버 전용).

upsert(table, rows, on_conflict): on_conflict 컬럼 기준 merge-duplicates 업서트.
재시도/백오프 포함(대량 동기화 견고화).
"""
from __future__ import annotations
import time
import requests
import config


def _headers():
    k = config.SUPABASE_SERVICE_ROLE_KEY
    return {
        "apikey": k,
        "Authorization": f"Bearer {k}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def upsert(table: str, rows: list, on_conflict: str):
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정 — auto/.env 확인")
    if not rows:
        return
    url = f"{config.SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    last = None
    # 5xx(503/525 등 Cloudflare·플랫폼 일시장애)·네트워크 오류는 재시도. 긴 백필 견고화:
    # 6회 시도 + 백오프 1·2·4·8·16초(최대 30) ≈ ~30초 창 → 순간 깜빡임 대부분 흡수.
    for attempt in range(6):
        try:
            r = requests.post(url, headers=_headers(), json=rows, timeout=120)
            if r.status_code in (200, 201, 204):
                return
            # 4xx(스키마/데이터 오류)는 재시도 무의미 → 즉시 노출
            if 400 <= r.status_code < 500:
                raise RuntimeError(f"{table} 업서트 {r.status_code}: {r.text[:400]}")
            last = f"{r.status_code}: {r.text[:200]}"
        except requests.exceptions.RequestException as e:
            last = str(e)
        if attempt < 5:
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"{table} 업서트 실패(재시도 초과): {last}")


def count(table: str) -> int:
    """행 수 확인(검증용)."""
    url = f"{config.SUPABASE_URL}/rest/v1/{table}?select=*"
    h = _headers()
    h["Prefer"] = "count=exact"
    h["Range"] = "0-0"
    r = requests.get(url, headers=h, timeout=60)
    cr = r.headers.get("content-range", "")
    return int(cr.split("/")[-1]) if "/" in cr else -1
