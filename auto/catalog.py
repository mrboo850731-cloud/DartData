"""DART 개발가이드 구조(6그룹 → 전체 API) 그대로의 커버리지 뷰어 — 자체 완결 HTML.

전체 카탈로그(85개 API)를 계층대로 펼치고, 각 API에 수집상태/건수를 표시.
수집된 API는 클릭해 레코드 테이블 → raw 필드까지 드릴다운.

실행:  python auto/catalog.py
산출:  auto/output/catalog.html
"""
from __future__ import annotations
import sys
import json
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config
import registry
from make_dashboard import _date_of, _detail, CLS

# ── 전체 카탈로그 (DART 개발가이드 순서 그대로) ──
# status: collected(수집) · driver(구동축) · excluded(바이너리제외) · phase2 · filinghub
DS001 = [
    ("공시검색", "driver", "수집 구동축(list.json)"),
    ("기업개황", "company", "회사 프로필"),
    ("공시서류 원본파일", "excluded", "ZIP 바이너리 → 제외"),
    ("고유번호", "excluded", "ZIP 시드 → 제외"),
]
DS002_NAMES = [
    "주식의 총수 현황", "자기주식 취득 및 처분 현황", "배당에 관한 사항", "증자(감자) 현황",
    "채무증권 발행실적", "기업어음증권 미상환 잔액", "단기사채 미상환 잔액", "회사채 미상환 잔액",
    "신종자본증권 미상환 잔액", "조건부 자본증권 미상환 잔액", "공모자금의 사용내역", "사모자금의 사용내역",
    "회계감사인의 명칭 및 감사의견", "감사용역체결현황", "비감사용역 계약체결 현황", "사외이사 및 그 변동현황",
    "최대주주 현황", "최대주주 변동현황", "소액주주 현황", "임원 현황", "직원 현황",
    "미등기임원 보수현황", "이사·감사 전체 보수(주총승인)", "이사·감사 전체 보수(지급-전체)",
    "이사·감사 전체 보수(유형별)", "이사·감사 개인별 보수(5억↑)", "〃 Ver2.0",
    "개인별 보수(5억↑ 상위5인)", "〃 Ver2.0", "타법인 출자현황",
]
DS003 = [
    ("단일회사 주요계정", "filinghub", "다중회사로 대체"),
    ("다중회사 주요계정", "filinghub", "FilingHub 재무 복사"),
    ("재무제표 원본파일(XBRL)", "excluded", "바이너리 → 제외"),
    ("다중회사 주요 재무지표", "filinghub", "FilingHub 재무 복사"),
    ("XBRL택사노미재무제표양식", "excluded", "참조표 → 제외"),
    ("단일회사 주요 재무지표", "filinghub", "다중회사로 대체"),
    ("단일회사 전체 재무제표", "filinghub", "FilingHub 재무 복사"),
]


YEARS = (2025, 2026)


def build():
    recs = []
    rng = []
    for y in YEARS:
        p = config.OUTPUT_DIR / f"events_{y}.json"
        if p.exists():
            dd = json.loads(p.read_text(encoding="utf-8"))
            recs += dd["records"]
            rng += dd.get("range", [])
    data = {"range": [min(rng), max(rng)] if rng else None}
    ncomp = 0
    cls_map = {}
    cpath = config.OUTPUT_DIR / "companies.json"
    if cpath.exists():
        cj = json.loads(cpath.read_text(encoding="utf-8"))
        ncomp = cj.get("count", 0)
        for c in cj.get("companies", []):
            if c.get("corp_code"):
                cls_map[c["corp_code"]] = c.get("corp_cls", "")

    # DS003 재무 (FilingHub 병합본) → 주요계정/재무지표 건수 + 샘플 드릴다운.
    fin_acct, fin_idx, n_acct, n_idx = [], [], 0, 0
    fpath = config.OUTPUT_DIR / "financials.json"
    if fpath.exists():
        fin = json.loads(fpath.read_text(encoding="utf-8"))
        fin = fin if isinstance(fin, list) else fin.get("records", [])

        def _fidx(r, kind):
            if kind == "acct":
                gs = [{"title": lab, "list": [r["acct"][fs]]}
                      for fs, lab in (("CFS", "연결(CFS) 주요계정"), ("OFS", "별도(OFS) 주요계정"))
                      if r.get("acct", {}).get(fs)]
            else:
                gs = [{"title": c, "list": [v]} for c, v in (r.get("idx") or {}).items() if v]
            cur = r.get("currency", "KRW")
            nm = r.get("corp_name", "") + (f" [{cur}]" if cur and cur != "KRW" else "")
            return {"nm": nm, "st": r.get("stock", ""),
                    "mk": CLS.get(cls_map.get(r.get("corp_code"), ""), ""),
                    "d": r.get("stlm_dt") or f"{r.get('year')}-{r.get('reprt')}",
                    "n": sum(len(g["list"][0]) for g in gs), "dt": _detail({"groups": gs})}

        acct_recs = [r for r in fin if r.get("acct", {}).get("CFS") or r.get("acct", {}).get("OFS")]
        idx_recs = [r for r in fin if r.get("idx")]
        n_acct, n_idx = len(acct_recs), len(idx_recs)
        acct_recs.sort(key=lambda r: r.get("stlm_dt", ""), reverse=True)
        idx_recs.sort(key=lambda r: r.get("stlm_dt", ""), reverse=True)
        fin_acct = [_fidx(r, "acct") for r in acct_recs[:150]]
        fin_idx = [_fidx(r, "idx") for r in idx_recs[:150]]

    # 엔드포인트별 레코드 묶음.
    by_ep = defaultdict(list)
    for r in recs:
        by_ep[r["endpoint"]].append({
            "nm": r["corp_name"], "st": r.get("stock", ""),
            "mk": CLS.get(r.get("cls", ""), ""), "d": _date_of(r),
            "n": r.get("n", 0), "dt": _detail(r),
        })
    for ep in by_ep:
        by_ep[ep].sort(key=lambda x: x["d"] or "", reverse=True)

    def api(name, status, note, endpoint=None):
        recs_ = by_ep.get(endpoint, []) if endpoint else []
        return {"name": name, "status": "collected" if recs_ else status,
                "note": note, "count": len(recs_), "recs": recs_}

    groups = []
    # DS001
    apis = []
    for nm, stt, note in DS001:
        if nm == "기업개황":
            apis.append({"name": nm, "status": "collected", "note": note,
                         "count": ncomp, "recs": []})  # 프로필은 별도 — 건수만
        else:
            apis.append({"name": nm, "status": stt, "note": note, "count": 0, "recs": []})
    groups.append({"g": "DS001", "t": "공시정보", "apis": apis})
    # DS002 (Phase 2 전체 보류)
    groups.append({"g": "DS002", "t": "정기보고서 주요정보",
                   "apis": [{"name": n, "status": "phase2", "note": "Phase 2 보류",
                             "count": 0, "recs": []} for n in DS002_NAMES]})
    # DS003 (재무 = FilingHub 병합본 → 실제 건수)
    groups.append({"g": "DS003", "t": "정기보고서 재무정보 (FilingHub 병합)", "apis": [
        {"name": "단일회사 주요계정", "status": "covered", "note": "다중회사로 충당", "count": 0, "recs": []},
        {"name": "다중회사 주요계정", "status": "collected" if n_acct else "pending",
         "note": "FilingHub 병합 (2023~)", "count": n_acct, "recs": fin_acct},
        {"name": "재무제표 원본파일(XBRL)", "status": "excluded", "note": "바이너리 → 제외", "count": 0, "recs": []},
        {"name": "다중회사 주요 재무지표", "status": "collected" if n_idx else "pending",
         "note": "FilingHub 병합 (2023 3분기~)", "count": n_idx, "recs": fin_idx},
        {"name": "XBRL택사노미재무제표양식", "status": "excluded", "note": "참조표 → 제외", "count": 0, "recs": []},
        {"name": "단일회사 주요 재무지표", "status": "covered", "note": "다중회사로 충당", "count": 0, "recs": []},
        {"name": "단일회사 전체 재무제표", "status": "pending", "note": "미수집 (필요시 on-demand)", "count": 0, "recs": []},
    ]})
    # DS004/005/006 (실수집)
    groups.append({"g": "DS004", "t": "지분공시 종합정보",
                   "apis": [api(l, "nodata", "2026 제출 없음", e) for e, l, k in registry.DS004]})
    groups.append({"g": "DS005", "t": "주요사항보고서 주요정보",
                   "apis": [api(l, "nodata", "2026 제출 없음", e) for e, l, k in registry.DS005]})
    groups.append({"g": "DS006", "t": "증권신고서 주요정보",
                   "apis": [api(l, "nodata", "2026 제출 없음", e) for e, l, k in registry.DS006]})

    return {"range": data.get("range"), "ncomp": ncomp, "nfin": n_acct,
            "total": len(recs), "groups": groups}


HTML = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>DartData 카탈로그</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:'Malgun Gothic',sans-serif;background:#0f172a;color:#e2e8f0}
.wrap{max-width:1100px;margin:0 auto;padding:24px}h1{font-size:21px;margin:0 0 4px}
.sub{color:#94a3b8;font-size:13px;margin-bottom:18px}
.grp{background:#1e293b;border:1px solid #334155;border-radius:12px;margin-bottom:14px;overflow:hidden}
.grp>summary{padding:14px 18px;font-size:16px;font-weight:700;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center}
.grp>summary::-webkit-details-marker{display:none}
.gcode{color:#38bdf8;font-size:12px;font-weight:600;margin-right:8px}
.api{border-top:1px solid #0f172a}
.api>summary{padding:9px 18px;cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;font-size:13px}
.api>summary::-webkit-details-marker{display:none}
.api>summary:hover{background:#27344b}
.api.no>summary{cursor:default;opacity:.7}
.badge{font-size:11px;padding:1px 8px;border-radius:99px;white-space:nowrap}
.b-collected{background:#065f46;color:#6ee7b7}.b-phase2{background:#3f3f46;color:#d4d4d8}
.b-filinghub{background:#1e3a8a;color:#93c5fd}.b-excluded{background:#3f1d1d;color:#fca5a5}
.b-driver{background:#3b2f0b;color:#fde68a}.b-nodata{background:#1e293b;color:#64748b;border:1px solid #334155}
.b-covered{background:#164e63;color:#67e8f9}.b-pending{background:#1e293b;color:#94a3b8;border:1px solid #334155}
.aname{flex:1}.cnt{color:#94a3b8;font-size:12px}.note{color:#64748b;font-size:11px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:6px 18px;border-bottom:1px solid #0f172a}
th{color:#94a3b8;position:sticky;top:0;background:#162033}
tr.row:hover{background:#27344b;cursor:pointer}.recwrap{max-height:420px;overflow:auto;background:#162033}
.detail{background:#0f172a}.detail td:first-child{color:#7dd3fc;width:280px;white-space:nowrap}
.muted{color:#64748b}.fc{color:#475569;font-size:10px;margin-left:5px}
</style></head><body><div class=wrap>
<h1>🗂️ DartData 카탈로그 (DART 개발가이드 구조)</h1>
<div class=sub id=sub></div>
<div id=root></div>
<div class=sub>범례: <span class="badge b-collected">수집 N건</span> <span class="badge b-nodata">제출없음</span>
<span class="badge b-phase2">Phase2 보류</span> <span class="badge b-covered">다중으로 충당</span> <span class="badge b-pending">미수집</span>
<span class="badge b-driver">구동축</span> <span class="badge b-excluded">제외(바이너리)</span></div>
</div>
<script>const D=%DATA%;
document.getElementById('sub').textContent=`6개 그룹 · 85개 API · 이벤트 ${D.total.toLocaleString()}건 (${(D.range||[]).join('~')}) · 재무 ${(D.nfin||0).toLocaleString()}건 · 기업개황 ${D.ncomp.toLocaleString()}사`;
const root=document.getElementById('root');
D.groups.forEach(g=>{
 const det=document.createElement('details');det.className='grp';if(['DS001','DS003','DS004','DS005','DS006'].includes(g.g))det.open=true;
 const nC=g.apis.filter(a=>a.status==='collected').length;
 det.innerHTML=`<summary><span><span class=gcode>${g.g}</span>${g.t}</span><span class=muted>${g.apis.length}개 API · 수집 ${nC}</span></summary>`;
 g.apis.forEach(a=>{
  const can=a.status==='collected'&&a.recs.length;
  const ad=document.createElement('details');ad.className='api'+(can?'':' no');
  const badge=a.status==='collected'?`<span class="badge b-collected">수집 ${a.count.toLocaleString()}건</span>`
   :a.status==='nodata'?`<span class="badge b-nodata">2026 제출없음</span>`
   :a.status==='phase2'?`<span class="badge b-phase2">Phase2 보류</span>`
   :a.status==='filinghub'?`<span class="badge b-filinghub">FilingHub 재무</span>`
   :a.status==='driver'?`<span class="badge b-driver">구동축</span>`
   :a.status==='covered'?`<span class="badge b-covered">다중으로 충당</span>`
   :a.status==='pending'?`<span class="badge b-pending">미수집</span>`
   :`<span class="badge b-excluded">제외</span>`;
  ad.innerHTML=`<summary><span class=aname>${a.name}</span>${badge}<span class=note>${a.note||''}</span></summary>`;
  if(can){
   const w=document.createElement('div');w.className='recwrap';
   w.innerHTML=`<table><thead><tr><th>날짜</th><th>회사</th><th>종목</th><th>시장</th><th>행수</th></tr></thead><tbody></tbody></table>`;
   const tb=w.querySelector('tbody');
   tb.innerHTML=a.recs.slice(0,800).map((r,i)=>`<tr class=row data-i=${i}><td>${r.d||''}</td><td>${r.nm}</td><td class=muted>${r.st||''}</td><td>${r.mk}</td><td class=muted>${r.n}</td></tr>`).join('');
   tb.addEventListener('click',e=>{const tr=e.target.closest('tr.row');if(!tr)return;const r=a.recs[+tr.dataset.i];
    const nx=tr.nextElementSibling;if(nx&&nx.classList.contains('detail')){nx.remove();return;}
    const d=document.createElement('tr');d.className='detail';
    d.innerHTML=`<td colspan=5><table>${(r.dt||[]).map(p=>`<tr><td>${p[0]}<span class=fc>${p[2]||''}</span></td><td>${p[1]}</td></tr>`).join('')}</table></td>`;tr.after(d);});
   ad.appendChild(w);
  }
  det.appendChild(ad);
 });
 root.appendChild(det);
});
</script></body></html>"""


def main():
    payload = build()
    html = HTML.replace("%DATA%", json.dumps(payload, ensure_ascii=False))
    out = config.OUTPUT_DIR / "catalog.html"
    out.write_text(html, encoding="utf-8")
    nc = sum(1 for g in payload["groups"] for a in g["apis"] if a["status"] == "collected")
    print(f"카탈로그 생성: {out} ({out.stat().st_size/1024:.0f} KB) · 수집된 API {nc}개")


if __name__ == "__main__":
    main()
