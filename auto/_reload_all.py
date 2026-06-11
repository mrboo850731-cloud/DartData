# -*- coding: utf-8 -*-
"""주석 전 분기 재적재 러너(120자 게이트 완화 + 금융업 role 인식 반영).
분리 프로세스로 실행, 진행은 _reload_all.log에 기록."""
import subprocess, sys, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "_reload_all.log")
JOBS = [("2026Q1", "2026", "11013"),   # cat() 수정 반영 위해 재실행
        ("2023Q3", "2023", "11014"), ("2023Q4", "2023", "11011"),
        ("2024Q1", "2024", "11013"), ("2024Q2", "2024", "11012"),
        ("2024Q3", "2024", "11014"), ("2024Q4", "2024", "11011"),
        ("2025Q1", "2025", "11013"), ("2025Q2", "2025", "11012"),
        ("2025Q3", "2025", "11014"), ("2025Q4", "2025", "11011")]


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("[%m-%d %H:%M:%S] ") + msg + "\n")


def main():
    open(LOG, "w", encoding="utf-8").close()
    t0 = time.time()
    for q, y, rp in JOBS:
        log(f"=== {q} 시작 ===")
        r = subprocess.run([sys.executable, os.path.join(HERE, "load_notes.py"),
                            "--dir", os.path.join(HERE, "..", "notes", q),
                            "--year", y, "--reprt", rp],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (r.stdout or "").strip().splitlines()[-1:] or ["(no output)"]
        log(f"{q} 종료(rc={r.returncode}): {tail[0]}")
        if r.returncode != 0:
            log("STDERR: " + (r.stderr or "")[-500:])
    log(f"=== 전체 완료 ({(time.time()-t0)/60:.0f}분) ===")
    log("DONE")


if __name__ == "__main__":
    main()
