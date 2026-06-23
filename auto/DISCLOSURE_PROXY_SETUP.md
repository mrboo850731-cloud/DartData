# 최근 공시 탭 — Vultr 프록시 셋업 런북

CF 엣지는 OpenDART에 IP 차단(연결 행)이라 직접 못 닿음 → 한국에서 도는 **Vultr 프록시**가 중계.
흐름: 브라우저 → CF Worker(`/api/disclosures`, 인증·캐시) → **api.disclo.co.kr**(cloudflared 터널)
→ `disclosure_proxy.py`(Vultr) → OpenDART. 키는 Vultr에만, CF↔Vultr는 공유 토큰으로 보호.

---

## 0. 프록시 코드를 GitHub(DartData main)에 올리기 — 내 PC에서
```
cd "C:\Users\부광우\Dropbox\문서\AI\DartData"
git add auto/disclosure_proxy.py auto/DISCLOSURE_PROXY_SETUP.md
git commit -m "disclosure_proxy: Vultr 공시검색 프록시"
git push origin main
```

## 1. 공유 토큰 만들기 (한 번, 값 복사 — Vultr·CF 양쪽에 같은 값 사용)
아무 데서나 한 줄 실행 후 출력값을 메모:
```
openssl rand -hex 32
```
(openssl 없으면 임의의 길고 무작위한 문자열 아무거나 — 64자 권장)

## 2. Vultr — 코드 pull + .env 두 줄 추가
```
cd /root/DartData && git pull
nano auto/.env
```
`.env` 맨 아래에 추가(저장: Ctrl+O→Enter→Ctrl+X):
```
DART_API_KEY_LIVE=<발급받은 전용 OpenDART 키>
DISCLOSURE_TOKEN=<1단계에서 만든 토큰>
```

## 3. Vultr — 프록시를 상시 서비스로(systemd)
`/etc/systemd/system/disclo-proxy.service` 생성:
```
[Unit]
Description=Disclo disclosure proxy
After=network.target

[Service]
WorkingDirectory=/root/DartData/auto
ExecStart=/usr/bin/python3 /root/DartData/auto/disclosure_proxy.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```
실행 + 로컬 확인:
```
systemctl daemon-reload
systemctl enable --now disclo-proxy
systemctl status disclo-proxy          # active (running) 확인
curl -s -H "X-Proxy-Token: <토큰>" "http://127.0.0.1:8787/disclosures?corp=00126380&page=1" | head -c 300
```
→ 삼성전자 공시 JSON이 나오면 프록시+키 정상.

## 4. cloudflared 터널로 api.disclo.co.kr 노출 (Cloudflare 대시보드 방식 권장)
대시보드 → **Zero Trust → Networks → Tunnels → Create a tunnel** → Cloudflared 선택 → 이름 `disclo-api`
→ 화면에 나오는 **설치/실행 명령(토큰 포함)을 Vultr에서 그대로 실행** → 커넥터 연결 확인
→ **Public Hostname 추가**: Subdomain `api`, Domain `disclo.co.kr`, Type `HTTP`, URL `localhost:8787` → 저장
확인(내 PC 등 아무 데서나):
```
curl -s -H "X-Proxy-Token: <토큰>" "https://api.disclo.co.kr/disclosures?corp=00126380&page=1" | head -c 300
```
→ 같은 JSON이 나오면 CF→Vultr 경로 완성.

## 5. CF Worker 시크릿 — 같은 토큰 등록
disclo-app → Settings → Variables and Secrets → Add → **Secret**, 이름 `DISCLOSURE_TOKEN`, 값 = 토큰.
(기존 `DART_API_KEY` Worker 시크릿은 이제 미사용 — 삭제하거나 둬도 무방.)

## 6. 배포 + 확인
오프라인 편집기 '웹사이트에 적용'(worker.js 반영) → www.disclo.co.kr → 삼성전자 → **최근 공시**.

---

### 점검 순서(안 뜰 때)
1. `systemctl status disclo-proxy` / `journalctl -u disclo-proxy -n 50`
2. 3단계 로컬 curl(127.0.0.1:8787) — 프록시·키 확인
3. 4단계 원격 curl(api.disclo.co.kr) — 터널 확인
4. Worker `DISCLOSURE_TOKEN`과 Vultr `.env` 토큰이 **완전히 동일**한지
5. 전용키 한도(전용키는 백필과 분리라 보통 여유)

상시 서비스 2개(disclo-proxy, cloudflared)가 Vultr에서 계속 떠 있어야 함.
