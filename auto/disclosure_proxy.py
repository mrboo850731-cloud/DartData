# -*- coding: utf-8 -*-
"""공시검색 프록시 (Vultr 상주) — Cloudflare Worker가 토큰 헤더로 호출하면 OpenDART list.json을 대리 조회.

배경: Cloudflare 엣지 IP는 금융감독원 OpenDART에 차단(연결 행)됨. 한국에서 도는 이 서버가 중계한다.
흐름: 브라우저 → CF Worker(/api/disclosures, 인증·캐시) → 이 서버(api.disclo.co.kr) → OpenDART.

- 127.0.0.1만 바인딩(공개 포트 없음) → cloudflared 터널로만 외부 노출.
- X-Proxy-Token 헤더로 인증(CF Worker와 공유). 키(DART_API_KEY_LIVE)는 이 서버에만 둔다(백필과 분리).
- 의존성: stdlib + requests(DartData에 이미 설치됨). .env는 자체 파서로 로드.

실행: python3 auto/disclosure_proxy.py   (systemd 권장 — RUNBOOK 참조)
"""
from __future__ import annotations
import os
import re
import json
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

KST = timezone(timedelta(hours=9))
DART_LIST = "https://opendart.fss.or.kr/api/list.json"


def _load_env(path):
    """.env(KEY=VALUE) 단순 로드 — dotenv 의존/ systemd EnvironmentFile 파싱 위험 회피."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

PORT = int(os.getenv("DISCLOSURE_PROXY_PORT", "8787"))
TOKEN = os.getenv("DISCLOSURE_TOKEN", "").strip()        # CF Worker와 공유하는 비밀 토큰(필수)
KEY = os.getenv("DART_API_KEY_LIVE", "").strip()         # 라이브 전용 OpenDART 키(백필 키와 분리)


def fetch(corp, page, bgn=None, end=None):
    today = datetime.now(KST).strftime("%Y%m%d")
    bgn = bgn if (bgn and re.fullmatch(r"\d{8}", bgn)) else "19990101"
    end = end if (end and re.fullmatch(r"\d{8}", end)) else today
    params = {"crtfc_key": KEY, "corp_code": corp, "bgn_de": bgn, "end_de": end,
              "page_no": str(page), "page_count": "100", "sort": "date", "sort_mth": "desc"}
    d = requests.get(DART_LIST, params=params, timeout=10).json()
    return {
        "status": d.get("status"), "message": d.get("message"),
        "page_no": d.get("page_no"), "total_page": d.get("total_page"),
        "total_count": d.get("total_count"),
        "list": [{"rcept_no": it.get("rcept_no"), "report_nm": it.get("report_nm"),
                  "flr_nm": it.get("flr_nm"), "rcept_dt": it.get("rcept_dt"),
                  "rm": it.get("rm")} for it in (d.get("list") or [])],
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, {"ok": True})
        if u.path != "/disclosures":
            return self._send(404, {"error": "not_found"})
        if not TOKEN or self.headers.get("X-Proxy-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        if not KEY:
            return self._send(503, {"error": "no_key"})
        q = parse_qs(u.query)
        corp = (q.get("corp", [""])[0]).strip()
        bgn = (q.get("bgn", [""])[0]).strip()       # YYYYMMDD(선택) — 기간 시작
        end = (q.get("end", [""])[0]).strip()       # YYYYMMDD(선택) — 기간 종료
        try:
            page = max(1, min(100, int(q.get("page", ["1"])[0])))
        except (ValueError, TypeError):
            page = 1
        if not re.fullmatch(r"\d{8}", corp):
            return self._send(400, {"error": "bad_corp"})
        try:
            return self._send(200, fetch(corp, page, bgn, end))
        except Exception:
            return self._send(502, {"error": "fetch_failed"})

    def log_message(self, *a):
        pass   # 액세스 로그 억제


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCLOSURE_TOKEN 미설정 — auto/.env 에 추가하세요.")
    if not KEY:
        print("[warn] DART_API_KEY_LIVE 미설정 — 요청 시 no_key 반환됨", flush=True)
    print("disclosure_proxy 127.0.0.1:%d 시작" % PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
