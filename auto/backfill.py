"""DartData 역사 백필 오케스트레이터 — Claude와 분리된 독립 실행(더블클릭 배치).

연도별로 이벤트(collect.py --no-ds004)와 재무(collect_financials.py)를 차례로 실행.
  - 일일 40,000콜 한도 자동 관리: 한도 근접 시 KST 자정 리셋까지 대기 후 재개.
  - 진행상황: 콘솔(라이브) + backfill.log + backfill_status.json → Claude가 읽어 보고/재개.
  - 각 수집기는 자체 체크포인트/재개. 오케스트레이터도 완료 타깃을 status에 기록 → 재시작 시 이어서.
  - Claude 세션과 무관한 별도 프로세스 → 세션 재시작에도 생존.

실행:  run_backfill.bat 더블클릭   (또는: python auto/backfill.py --used 31000)
       --used = 오늘 이미 쓴 콜수(시작 시점). 자정 리셋 후엔 0으로 재계산.
"""
from __future__ import annotations
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config

KST = timezone(timedelta(hours=9))
PY = sys.executable
LOG = config.OUTPUT_DIR / "backfill.log"
STATUS = config.OUTPUT_DIR / "backfill_status.json"
LIMIT = 40000
BUFFER = 3000
EST = {"event": 3700, "fin": 500}      # 타깃당 예상 콜(실측 반영: 재무는 다중배치라 ~334)

# 타깃: 이벤트 2024→2015, 재무 2022→2015 (2025·2026 이벤트, 2023~2026 재무는 이미 보유).
TARGETS = []
for y in range(2024, 2014, -1):
    TARGETS.append(("event", y))
    if y <= 2022:
        TARGETS.append(("fin", y))


def now():
    return datetime.now(KST)


def log(m):
    line = f"[{now():%m-%d %H:%M:%S}] {m}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_status():
    if STATUS.exists():
        try:
            return json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": [], "calls_today": 0, "day": now().strftime("%Y-%m-%d"),
            "current": None, "seeded": False, "total_targets": len(TARGETS)}


def save_status(s):
    s["updated"] = now().strftime("%Y-%m-%d %H:%M:%S")
    s["total_targets"] = len(TARGETS)
    s["remaining"] = [f"{k}:{y}" for k, y in TARGETS if f"{k}:{y}" not in s["done"]]
    STATUS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def wait_until_midnight(s):
    nxt = (now() + timedelta(days=1)).replace(hour=0, minute=2, second=0, microsecond=0)
    log(f"일일 한도 근접 → KST 자정 리셋까지 대기 (~{(nxt-now()).total_seconds()/3600:.1f}h)")
    while now() < nxt:
        time.sleep(min(300, max(5, (nxt - now()).total_seconds())))
    s["day"] = now().strftime("%Y-%m-%d")
    s["calls_today"] = 0
    log("자정 리셋 — 한도 초기화, 재개")


def run_target(kind, year):
    if kind == "event":
        cmd = [PY, "-u", str(ROOT / "collect.py"), f"{year}0101", f"{year}1231", "--no-ds004"]
    else:
        cmd = [PY, "-u", str(ROOT / "collect_financials.py"), str(year)]
    try:
        return subprocess.run(cmd).returncode
    except Exception as e:
        log(f"  subprocess 예외: {e}")
        return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--used", type=int, default=0, help="시작 시점 오늘 이미 쓴 콜수")
    ap.add_argument("--dry-run", action="store_true", help="실행 계획만 출력(수집 안 함)")
    args = ap.parse_args()

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    s = load_status()

    if args.dry_run:
        log(f"[DRY-RUN] 타깃 {len(TARGETS)}개 · 완료 {len(s['done'])} · 오늘사용~{s.get('calls_today',0)}")
        for k, y in TARGETS:
            mark = "✔" if f"{k}:{y}" in s["done"] else "·"
            log(f"  {mark} {k}:{y} (예상 {EST[k]}콜)")
        log(f"[DRY-RUN] 총 예상 ~{sum(EST[k] for k,_ in TARGETS):,}콜 · 일한도 {LIMIT} → 자정대기로 분할")
        save_status(s)
        return
    if not s.get("seeded"):
        s["calls_today"] = max(s.get("calls_today", 0), args.used)
        s["day"] = now().strftime("%Y-%m-%d")
        s["seeded"] = True
    save_status(s)
    log(f"===== 백필 시작 — 완료 {len(s['done'])}/{len(TARGETS)} · 오늘 사용 ~{s['calls_today']}콜 =====")

    for kind, year in TARGETS:
        key = f"{kind}:{year}"
        if key in s["done"]:
            continue
        # 날짜 변경 시 카운터 리셋
        if now().strftime("%Y-%m-%d") != s["day"]:
            s["day"] = now().strftime("%Y-%m-%d")
            s["calls_today"] = 0
            log("날짜 변경 — 한도 카운터 리셋")
        # 한도 근접 시 자정까지 대기
        if s["calls_today"] + EST[kind] > LIMIT - BUFFER:
            save_status(s)
            wait_until_midnight(s)

        s["current"] = key
        save_status(s)
        t0 = time.time()
        log(f"▶ {key} 시작")
        rc = run_target(kind, year)
        s["calls_today"] += EST[kind]
        if rc == 0:
            s["done"].append(key)
            s["current"] = None
            log(f"✔ {key} 완료 ({time.time()-t0:.0f}s · 오늘 누적 ~{s['calls_today']}콜)")
        else:
            log(f"✘ {key} 비정상종료(rc={rc}) — done 미표기, 다음 실행 때 재개")
        save_status(s)
        time.sleep(2)

    s["current"] = "DONE"
    save_status(s)
    log(f"🎉 전체 백필 완료 — {len(s['done'])}/{len(TARGETS)} 타깃")


if __name__ == "__main__":
    main()
