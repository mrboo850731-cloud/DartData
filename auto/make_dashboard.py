"""로컬 수집 데이터 → 자체 완결 HTML 대시보드 (서버 불필요, 더블클릭 실행).

events_{year}.json (이벤트) + companies.json (기업개황) 을 읽어
요약·분포·검색가능 테이블(행 클릭 시 raw 필드 미리보기)을 한 파일에 임베드.

실행:  python auto/make_dashboard.py [year]   (기본 2026)
산출:  auto/output/dashboard.html
"""
from __future__ import annotations
import sys
import json
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config
try:
    import field_labels
    _LAB = field_labels.LABELS
except Exception:
    _LAB = {}

CLS = {"Y": "코스피", "K": "코스닥", "N": "코넥스", "E": "비상장", "": "기타"}


def _date_of(rec):
    """레코드에서 대표 접수일(rcept_no 앞 8자리) 추출."""
    def first_rows(r):
        if r.get("rows"):
            return r["rows"]
        for g in r.get("groups", []):
            if g.get("list"):
                return g["list"]
        return []
    for row in first_rows(rec):
        rc = str(row.get("rcept_no", ""))
        if len(rc) >= 8 and rc[:8].isdigit():
            return f"{rc[:4]}-{rc[4:6]}-{rc[6:8]}"
    return rec.get("params", {}).get("bgn_de", "")


_SKIP_REPEAT = {"rcept_no", "corp_cls", "corp_code", "corp_name"}


def _detail(rec, max_rows=2, max_items=25, max_fields=24, vlen=70):
    """행 클릭 시 raw 필드 → [한글명, 값, 영문코드]. 그룹 항목(전 트랜치 등)은 모두 표시.

    평면 rows(DS004 등 다건)는 max_rows 까지만(+'외 N건'). 그룹형(증권신고서 트랜치)은
    max_items 까지. 공통 식별필드는 첫 항목에만 1회 표기해 중복 노이즈 축소.
    """
    out = []
    state = {"first": True}

    def emit(item):
        for k, v in list(item.items())[:max_fields]:
            if not state["first"] and k in _SKIP_REPEAT:
                continue
            out.append([_LAB.get(k, k), str(v)[:vlen], k])
        state["first"] = False

    rows = rec.get("rows") or []
    if rows:
        for i, row in enumerate(rows[:max_rows]):
            if len(rows) > 1:
                out.append([f"── #{i + 1} / {len(rows)}건 ──", "", ""])
            emit(row)
        if len(rows) > max_rows:
            out.append([f"… 외 {len(rows) - max_rows}건 (전체 raw에 보존)", "", ""])

    for g in rec.get("groups", []):
        lst = g.get("list") or []
        out.append([f"▼ {g.get('title', '')} · {len(lst)}건", "", ""])
        for i, item in enumerate(lst[:max_items]):
            if len(lst) > 1:
                out.append([f"  · {i + 1}/{len(lst)}", "", ""])
            emit(item)
        if len(lst) > max_items:
            out.append([f"  … 외 {len(lst) - max_items}건", "", ""])
    return out


def build(years=(2025, 2026)):
    recs = []
    rng = []
    for y in years:
        p = config.OUTPUT_DIR / f"events_{y}.json"
        if p.exists():
            dd = json.loads(p.read_text(encoding="utf-8"))
            recs += dd["records"]
            rng += dd.get("range", [])
    data = {"range": [min(rng), max(rng)] if rng else None}

    comp = []
    cpath = config.OUTPUT_DIR / "companies.json"
    if cpath.exists():
        comp = json.loads(cpath.read_text(encoding="utf-8")).get("companies", [])

    GLABEL = {"DS004": "지분공시", "DS005": "주요사항", "DS006": "증권신고서"}
    by_group = Counter(r["group"] for r in recs)
    by_mkt = Counter(r.get("cls", "") for r in recs)
    by_ep = Counter((r["group"], r["label"]) for r in recs)
    top_corp = Counter((r["corp_name"], r.get("stock", ""), r.get("cls", "")) for r in recs)
    total_rows = sum(r.get("n", 0) for r in recs)

    # 임베드용 인덱스 (가벼움) + 행별 detail 미리보기.
    index = []
    for r in recs:
        index.append({
            "nm": r["corp_name"], "st": r.get("stock", ""),
            "mk": CLS.get(r.get("cls", ""), ""), "g": r["group"],
            "lb": r["label"], "d": _date_of(r), "n": r.get("n", 0),
            "dt": _detail(r),
        })
    index.sort(key=lambda x: (x["d"] or ""), reverse=True)

    payload = {
        "range": data.get("range"),
        "total": len(recs), "rows": total_rows, "ncomp": len(comp),
        "byGroup": [[GLABEL.get(g, g), c] for g, c in by_group.most_common()],
        "byMkt": [[CLS.get(m, m), c] for m, c in by_mkt.most_common()],
        "byEp": [[GLABEL.get(g, g), l, c] for (g, l), c in by_ep.most_common(20)],
        "topCorp": [[nm, st, CLS.get(cl, ""), c] for (nm, st, cl), c in top_corp.most_common(20)],
        "glabel": GLABEL,
        "index": index,
    }
    return payload


HTML = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>DartData 대시보드</title>
<style>
*{box-sizing:border-box} body{margin:0;font-family:'Malgun Gothic',-apple-system,sans-serif;background:#0f172a;color:#e2e8f0}
.wrap{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px} .sub{color:#94a3b8;font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px}
.card .v{font-size:26px;font-weight:700;color:#38bdf8} .card .k{font-size:12px;color:#94a3b8;margin-top:4px}
.panel{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px;margin-bottom:20px}
.panel h2{font-size:14px;margin:0 0 12px;color:#cbd5e1}
.bar{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:13px}
.bar .lab{width:160px;color:#cbd5e1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .track{flex:1;background:#0f172a;border-radius:5px;overflow:hidden;height:18px}
.bar .fill{height:100%;background:linear-gradient(90deg,#0ea5e9,#6366f1)}
.bar .num{width:60px;text-align:right;color:#94a3b8}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:780px){.cols{grid-template-columns:1fr}}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
input,select{background:#0f172a;border:1px solid #334155;color:#e2e8f0;border-radius:8px;padding:8px 10px;font-size:13px}
input{flex:1;min-width:180px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #1e293b}
th{color:#94a3b8;font-weight:600;position:sticky;top:0;background:#1e293b;cursor:pointer}
tr.row:hover{background:#27344b;cursor:pointer}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;background:#334155}
.g-DS004{color:#34d399} .g-DS005{color:#fbbf24} .g-DS006{color:#f472b6}
.detail{background:#0f172a;font-size:12px;color:#cbd5e1}
.detail table{font-size:12px} .detail td:first-child{color:#7dd3fc;width:240px;white-space:nowrap}
.muted{color:#64748b} .scroll{max-height:560px;overflow:auto}
.fc{color:#475569;font-size:10px;margin-left:5px}
</style></head><body><div class=wrap>
<h1>📊 DartData 수집 대시보드</h1>
<div class=sub id=sub></div>
<div class=cards id=cards></div>
<div class=cols>
<div class=panel><h2>그룹별 레코드</h2><div id=cg></div></div>
<div class=panel><h2>시장별 레코드</h2><div id=cm></div></div>
</div>
<div class=cols>
<div class=panel><h2>항목(상위 20)</h2><div id=ce></div></div>
<div class=panel><h2>이벤트 많은 회사 (상위 20)</h2><div id=ct></div></div>
</div>
<div class=panel>
<h2>전체 레코드 (행 클릭 → raw 필드)</h2>
<div class=controls>
<input id=q placeholder="회사명 검색…">
<select id=fg><option value="">전체 그룹</option><option>DS004</option><option>DS005</option><option>DS006</option></select>
<select id=fm><option value="">전체 시장</option><option>코스피</option><option>코스닥</option><option>코넥스</option><option>비상장</option></select>
<span class=muted id=cnt></span>
</div>
<div class=scroll><table><thead><tr><th>날짜</th><th>회사</th><th>종목</th><th>시장</th><th>그룹</th><th>항목</th><th>행수</th></tr></thead><tbody id=tb></tbody></table></div>
</div>
<div class=sub>※ raw JSONB 무손실 저장본의 미리보기. 그룹형(트랜치 등)은 전부, 평면형(소유보고 등 다건)은 일부만 표시(전체는 원본 보존).</div>
</div>
<script>const D=%DATA%;
const $=s=>document.querySelector(s);
$('#sub').textContent=`기간 ${(D.range||[]).join(' ~ ')} · 이벤트 ${D.total.toLocaleString()}건 · raw ${D.rows.toLocaleString()}행 · 기업개황 ${D.ncomp.toLocaleString()}사`;
$('#cards').innerHTML=[['이벤트 레코드',D.total],['총 raw 행',D.rows],['기업개황',D.ncomp],['그룹',D.byGroup.length],['항목 종류',D.byEp.length]].map(([k,v])=>`<div class=card><div class=v>${v.toLocaleString()}</div><div class=k>${k}</div></div>`).join('');
function bars(el,rows,max){const m=Math.max(...rows.map(r=>r[r.length-1]));el.innerHTML=rows.map(r=>{const n=r[r.length-1],lab=r.slice(0,-1).join(' · ');return `<div class=bar><span class=lab title="${lab}">${lab}</span><span class=track><span class=fill style="width:${100*n/m}%"></span></span><span class=num>${n.toLocaleString()}</span></div>`}).join('')}
bars($('#cg'),D.byGroup);bars($('#cm'),D.byMkt);bars($('#ce'),D.byEp);bars($('#ct'),D.topCorp);
const tb=$('#tb');let cur=D.index;
function render(){const q=$('#q').value.trim(),g=$('#fg').value,m=$('#fm').value;
cur=D.index.filter(r=>(!q||r.nm.includes(q))&&(!g||r.g===g)&&(!m||r.mk===m)).slice(0,1500);
$('#cnt').textContent=`${cur.length.toLocaleString()}건 표시`;
tb.innerHTML=cur.map((r,i)=>`<tr class=row data-i=${i}><td>${r.d||''}</td><td>${r.nm}</td><td class=muted>${r.st||''}</td><td>${r.mk}</td><td class=g-${r.g}>${r.g}</td><td>${r.lb}</td><td class=muted>${r.n}</td></tr>`).join('');}
tb.addEventListener('click',e=>{const tr=e.target.closest('tr.row');if(!tr)return;const i=+tr.dataset.i,r=cur[i];
const nx=tr.nextElementSibling;if(nx&&nx.classList.contains('detail')){nx.remove();return;}
const d=document.createElement('tr');d.className='detail';
d.innerHTML=`<td colspan=7><table>${(r.dt||[]).map(p=>`<tr><td>${p[0]}<span class=fc>${p[2]||''}</span></td><td>${p[1]}</td></tr>`).join('')||'<tr><td>(필드 없음)</td></tr>'}</table></td>`;
tr.after(d);});
$('#q').oninput=render;$('#fg').onchange=render;$('#fm').onchange=render;render();
</script></body></html>"""


def main():
    payload = build()
    html = HTML.replace("%DATA%", json.dumps(payload, ensure_ascii=False))
    out = config.OUTPUT_DIR / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    print(f"대시보드 생성: {out}  ({out.stat().st_size/1024:.0f} KB)")
    print(f"  이벤트 {payload['total']:,} · raw {payload['rows']:,}행 · 기업개황 {payload['ncomp']:,}사")


if __name__ == "__main__":
    main()
