# -*- coding: utf-8 -*-
"""'기타'로 분류돼 유실되는 role명 카탈로그 — cat() 패턴 보완 근거."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from load_notes import blocks, Cursor, cat, is_num
from collections import Counter

D = os.path.join(os.path.dirname(__file__), "..", "notes", "2026Q1")
P = lambda n: os.path.join(D, n)
valg = blocks(P("val.tsv"))
prec, rolc = Cursor(blocks(P("pre.tsv"))), Cursor(blocks(P("role.tsv")))

other_roles = Counter()       # '기타' 분류 role명 — 숫자 fact가 걸린 것만
done = 0
for cik, vrows in valg:
    prerows, rolerows = prec.get(cik), rolc.get(cik)
    rname, rcat = {}, {}
    for r in rolerows:
        if len(r) >= 6:
            rname[r[3]] = r[5]; rcat[r[3]] = cat(r[5])
    er = {}
    for r in prerows:
        if len(r) >= 5:
            er.setdefault((r[3], r[4]), []).append(r[2])
    for r in vrows:
        if len(r) < 8 or not is_num(r[7]):
            continue
        rids = er.get((r[2], r[3]), [])
        cats = {rcat.get(rid) for rid in rids}
        if "주석" in cats or "본문" in cats or "문서" in cats:
            continue
        for rid in rids:
            if rcat.get(rid) == "기타":
                other_roles[rname.get(rid, "?")[:70]] += 1
    done += 1
    if done >= 80:
        break

print(f"=== {done}개사 — '기타' role명 상위(숫자 fact 기준) ===")
for k, c in other_roles.most_common(20):
    print(f"  {c:>7,}  {k}")
