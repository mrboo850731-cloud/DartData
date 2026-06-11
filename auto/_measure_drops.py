# -*- coding: utf-8 -*-
"""적재 시 버려지는 항목 통계(2026Q1, 앞 N개사) — 잔여 유실 점검.
분류: ①미매핑 숫자(주석 role 매핑 실패 — 유실 위험!) ②주석 텍스트 ≤20자(의도적 드랍)
③비주석 짧은 텍스트(본문/문서 — 의도적) ④보존되는 것들."""
import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))
from load_notes import blocks, Cursor, cat, title_of, is_num
from collections import Counter

D = os.path.join(os.path.dirname(__file__), "..", "notes", "2026Q1")
P = lambda n: os.path.join(D, n)
valg = blocks(P("val.tsv"))
prec, rolc, labc, ctxc = (Cursor(blocks(P(n))) for n in ("pre.tsv", "role.tsv", "lab.tsv", "cntxt.tsv"))

N = 80
stats = Counter()
unmapped_labels = Counter()
short_note_text = Counter()
done = 0
for cik, vrows in valg:
    prerows, rolerows, labrows = prec.get(cik), rolc.get(cik), labc.get(cik)
    ctxc.get(cik)
    rname, rcat = {}, {}
    for r in rolerows:
        if len(r) >= 6:
            rname[r[3]] = r[5]; rcat[r[3]] = cat(r[5])
    er = {}
    for r in prerows:
        if len(r) >= 5:
            er.setdefault((r[3], r[4]), []).append(r[2])
    ko = {}
    for r in labrows:
        if len(r) >= 7 and r[5] == "ko":
            ko.setdefault((r[3], r[4]), r[6])

    def note_sec(eid, tax):
        for rid in er.get((eid, tax), []):
            if rcat.get(rid) == "주석":
                return rname.get(rid, "")
        return None

    for r in vrows:
        if len(r) < 8:
            continue
        eid, tax, v = r[2], r[3], r[7]
        sec = note_sec(eid, tax)
        if is_num(v):
            if sec is None:
                # 미매핑 숫자 — 어떤 role인가(본문 D2~D5 중복인지, 진짜 유실인지)
                rids = er.get((eid, tax), [])
                cats = {rcat.get(rid) or "롤없음" for rid in rids} or {"롤없음"}
                key = ",".join(sorted(cats))
                stats["숫자·미매핑(" + key + ")"] += 1
                if "본문" not in key and "문서" not in key:
                    unmapped_labels[(ko.get((eid, tax)) or eid.split("_")[-1])[:30]] += 1
            else:
                stats["숫자·주석(보존)"] += 1
        else:
            if "<" in v or len(v) > 120:
                stats["텍스트·보존(기존게이트)"] += 1
            elif sec and len(v) > 20:
                stats["텍스트·보존(신규회수 21~120자)"] += 1
            elif sec:
                stats["텍스트·주석 ≤20자(드랍·의도)"] += 1
                short_note_text[v.strip()[:20]] += 1
            else:
                stats["텍스트·비주석 짧음(드랍·의도)"] += 1
    done += 1
    if done >= N:
        break

print(f"=== {done}개사 드랍 통계 ===")
tot = sum(stats.values())
for k, c in stats.most_common():
    print(f"  {k:42s} {c:>10,} ({c/tot*100:.1f}%)")
print("\n=== 미매핑 숫자(본문·문서 외) 라벨 상위 — 진짜 유실 후보 ===")
for k, c in unmapped_labels.most_common(15):
    print(f"  {c:>6,}  {k}")
print("\n=== ≤20자 주석 텍스트 샘플(의도 드랍 — 확인용) ===")
for k, c in short_note_text.most_common(10):
    print(f"  {c:>6,}  {k!r}")
