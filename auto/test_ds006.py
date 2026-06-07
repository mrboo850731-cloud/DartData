"""DS006 진단 — 실제 2026 증권신고서 제출사로 estkRs/bdRs 동작 확인."""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config, dart_api

corps_bd, corps_es = [], []
for cs, ce in [("20260101", "20260331"), ("20260401", "20260606")]:
    page = 1
    while True:
        d = dart_api.list_disclosures(cs, ce, pblntf_ty="C", page_no=page, page_count=100)
        if d.get("status") == "013":
            break
        for it in d.get("list", []):
            nm = it.get("report_nm", "")
            if "증권신고서(채무증권)" in nm and len(corps_bd) < 4:
                corps_bd.append((it["corp_code"], it["corp_name"]))
            if "증권신고서(지분증권)" in nm and len(corps_es) < 4:
                corps_es.append((it["corp_code"], it["corp_name"]))
        tp = int(d.get("total_page", 1) or 1)
        if page >= tp or (len(corps_bd) >= 4 and len(corps_es) >= 4):
            break
        page += 1
        time.sleep(0.1)

print("채무증권 제출사:", corps_bd)
print("지분증권 제출사:", corps_es, "\n")


def t(ep, corp, name):
    print(f"[{ep}] {name} ({corp})")
    for rng in [("20260101", "20260606"), ("20200101", "20260606"), None]:
        p = {"corp_code": corp}
        if rng:
            p.update(bgn_de=rng[0], end_de=rng[1])
        try:
            data = dart_api._get(f"{config.API_BASE}/{ep}.json", p)
            print(f"   범위 {rng}: status={data.get('status')} rows={len(data.get('list', []))}")
        except Exception as e:
            print(f"   범위 {rng}: ERR {e}")


for corp, nm in corps_bd[:2]:
    t("bdRs", corp, nm)
for corp, nm in corps_es[:2]:
    t("estkRs", corp, nm)
