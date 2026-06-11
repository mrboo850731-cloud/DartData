"""재무제표 주석 로더 — OpenDART '주석 일괄다운로드' XBRL(11 tsv) → Supabase financials_notes.

회사(CIK=corp_code)당 JSONB 1행: 서술형 주석 + 주석섹션 숫자(라벨·값·기간·차원·섹션).
본문(재무제표 D2~D5)·문서정보 제외(중복). financials_full 과 (corp_code,year,reprt) 조인.
입력 11파일은 모두 CIK 정렬 → lockstep 스트리밍(회사 1곳씩, 메모리 절약).

사용:
  python load_notes.py --dir ../notes/2026Q1 --year 2026 --reprt 11013 --measure --sample 3
  python load_notes.py --dir ../notes/2026Q1 --year 2026 --reprt 11013
"""
from __future__ import annotations
import sys
import os
import re
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import requests
import config
import supabase_client as sb

KST = timezone(timedelta(hours=9))


def log(m):
    print(f"[{datetime.now(KST):%m-%d %H:%M:%S}] {m}", flush=True)


def cat(nm):
    # 업종 변형 접두 허용: [D839005] 일반 외에 [DS…]증권·[DI…]보험·[DB…]은행 등
    # 금융업 role은 D 뒤에 업종 문자가 끼어 기존 패턴(D+숫자)에서 전부 '기타'로 떨어져
    # 금융업 주석이 통째로 유실됐었음(예: [DS839005] 특수관계자와의 거래). 첫 숫자가 구분자.
    m = re.search(r"[\[\-]([DU])([A-Z]?)(\d)", nm or "")
    if not m:
        return "기타"
    p, d = m.group(1), m.group(3)
    if p == "D" and d in "23456":     # D2~D6 = 재무제표 본문(BS·IS·CIS·CF·자본변동표) = financials_full에 있음
        return "본문"
    if p == "D" and d in "78":        # D7·D8 = 주석
        return "주석"
    if p == "D" and d == "9":
        return "문서"
    if p == "U":                       # U7·U8 = 한국 특유 주석 공시(우발사항·약정 등)
        return "주석"
    return "기타"


def title_of(nm):
    m = re.match(r"\s*\[[DU]\d+\]\s*(.*)", nm or "")
    return (m.group(1).strip() if m else (nm or "")).strip()


def is_num(v):
    try:
        float(v.replace(",", ""))
        return True
    except Exception:
        return False


def blocks(path, cikidx=0):
    with open(path, encoding="utf-8-sig") as f:
        f.readline()
        cur, buf = None, []
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) <= cikidx:
                continue
            k = c[cikidx]
            if cur is None:
                cur = k
            if k != cur:
                yield cur, buf
                cur, buf = k, [c]
            else:
                buf.append(c)
        if buf:
            yield cur, buf


class Cursor:
    """CIK 정렬 파일에서 target 회사 블록만 뽑기(드라이버=val 기준 전진)."""
    def __init__(self, gen):
        self.gen = gen
        self.cur = None

    def get(self, target):
        while True:
            if self.cur is None:
                try:
                    self.cur = next(self.gen)
                except StopIteration:
                    return []
            k, b = self.cur
            if k == target:
                self.cur = None
                return b
            if k < target:
                self.cur = None
                continue
            return []


def build_company(cik, vrows, prerows, rolerows, labrows, ctxrows):
    rname, rcat = {}, {}
    for r in rolerows:
        if len(r) >= 6:
            rname[r[3]] = r[5]
            rcat[r[3]] = cat(r[5])
    er = {}
    for r in prerows:
        if len(r) >= 5:
            er.setdefault((r[3], r[4]), []).append(r[2])
    ko = {}
    for r in labrows:
        if len(r) >= 7 and r[5] == "ko":
            ko.setdefault((r[3], r[4]), r[6])
    ctx = {}
    for r in ctxrows:
        if len(r) >= 11:
            d = ctx.setdefault(r[2], {"ps": r[8], "pe": r[9], "pi": r[10], "dims": []})
            if r[5]:
                d["dims"].append((r[3], r[4], r[5], r[6]))

    def note_sec(eid, tax):
        for rid in er.get((eid, tax), []):
            if rcat.get(rid) == "주석":
                return title_of(rname.get(rid, "")) or rname.get(rid, "")
        return None

    def period_dim(cid):
        c = ctx.get(cid, {})
        p = c.get("pi") or (f'{c.get("ps","")}~{c.get("pe","")}' if c.get("ps") else "")
        dims = []
        for ae, at, me, mt in c.get("dims", []):
            al = ko.get((ae, at)) or ae.split("_")[-1]
            ml = ko.get((me, mt)) or me.split("_")[-1]
            dims.append(f"{al}={ml}")
        return p, ("; ".join(dims) if dims else "")

    narrative, facts, rdate = [], [], ""
    for r in vrows:
        if len(r) < 8:
            continue
        rdate = r[1] or rdate
        eid, tax, cid, v = r[2], r[3], r[4], r[7]
        label = ko.get((eid, tax)) or eid.split("_")[-1]
        if is_num(v):
            sec = note_sec(eid, tax)
            if sec is None:          # 본문/문서/미매핑 숫자 제외
                continue
            p, dim = period_dim(cid)
            facts.append({"sec": sec, "label": label, "val": v, "period": p, "dim": dim})
        else:
            # 서술형 주석. 길이 120자 게이트만 쓰면 주석 섹션의 짧은 평문 설명이 유실됨 —
            # 실사례: LG에너지솔루션 '이자율이 1%(100bp) 변동시 …'(79자, 태그 없음)가 탈락해
            # 민감도 변동 폭 가정이 DB에서 사라짐(환율 버전은 태그 포함이라 생존). 주석 섹션에
            # 매핑된 텍스트는 20자 초과면 보존(코드값·'해당사항 없음' 등 잡음만 차단).
            sec = note_sec(eid, tax)
            if "<" in v or len(v) > 120 or (sec and len(v) > 20):
                narrative.append({"sec": sec or "", "label": label, "html": v})
    return rdate, narrative, facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--reprt", required=True)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--measure", action="store_true", help="업로드 없이 샘플 dump")
    a = ap.parse_args()
    D = a.dir

    def P(n):
        return os.path.join(D, n)

    valg = blocks(P("val.tsv"))
    prec, rolc, labc, ctxc = (Cursor(blocks(P(n))) for n in
                              ("pre.tsv", "role.tsv", "lab.tsv", "cntxt.tsv"))

    t0 = time.time()
    buf, buf_bytes, done = [], 0, 0
    tot_narr = tot_facts = 0
    sample_rows = []
    for cik, vrows in valg:
        rdate, narrative, facts = build_company(
            cik, vrows, prec.get(cik), rolc.get(cik), labc.get(cik), ctxc.get(cik))
        if not narrative and not facts:
            continue
        secs = set(f["sec"] for f in facts) | set(n["sec"] for n in narrative if n["sec"])
        row = {"corp_code": cik, "year": a.year, "reprt": a.reprt, "rdate": rdate,
               "n_narr": len(narrative), "n_facts": len(facts), "n_sec": len(secs),
               "notes": {"rdate": rdate, "narrative": narrative, "facts": facts}}
        tot_narr += len(narrative)
        tot_facts += len(facts)
        done += 1
        if a.measure:
            if len(sample_rows) < (a.sample or 3):
                sample_rows.append(row)
            if a.sample and len(sample_rows) >= a.sample:
                break
            continue
        b = len(json.dumps(row, ensure_ascii=False).encode())
        buf.append(row)
        buf_bytes += b
        if buf_bytes > 3_000_000 or len(buf) >= 30:
            sb.upsert("financials_notes", buf, "corp_code,year,reprt")
            buf, buf_bytes = [], 0
        if done % 200 == 0:
            log(f"  {done} 회사 (narr {tot_narr:,} / facts {tot_facts:,})")
    if not a.measure and buf:
        sb.upsert("financials_notes", buf, "corp_code,year,reprt")

    log(f"=== {'측정' if a.measure else '적재'} {done} 회사 / narr {tot_narr:,} / facts {tot_facts:,} ({time.time()-t0:.0f}s) ===")

    if a.measure:
        _dump_sample(sample_rows, a)


def _dump_sample(rows, a):
    out = []
    # 조인 검증: 이 회사들이 financials_full 에 있는지
    H = {"apikey": config.SUPABASE_SERVICE_ROLE_KEY,
         "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}"}
    for row in rows:
        cik = row["corp_code"]
        joined = "?"
        try:
            r = requests.get(config.SUPABASE_URL + "/rest/v1/financials_full", headers=H,
                             params={"select": "corp_code,corp_name,n_cfs",
                                     "corp_code": f"eq.{cik}", "year": f"eq.{a.year}",
                                     "reprt": f"eq.{a.reprt}"}, timeout=20)
            j = r.json()
            joined = (f"있음 ({j[0].get('corp_name','')}, 본문 {j[0].get('n_cfs')}계정)"
                      if j else "financials_full에 없음(상폐/미제출?)")
        except Exception as e:
            joined = f"조회실패 {e}"
        out.append(f"{'='*70}")
        out.append(f"corp_code={cik}  rdate={row['rdate']}  섹션 {row['n_sec']}  서술 {row['n_narr']}  숫자 {row['n_facts']}")
        out.append(f"  ↔ financials_full 조인: {joined}")
        out.append("")
        out.append(f"  [서술형 주석 {row['n_narr']}건 중 앞 4]")
        for n in row["notes"]["narrative"][:4]:
            snip = " ".join(n["html"].split())[:200]
            out.append(f"   ■ ({n['sec']}) {n['label']}: {snip}…")
        out.append("")
        out.append(f"  [주석숫자 {row['n_facts']}건 중 앞 12]")
        for f in row["notes"]["facts"][:12]:
            d = f"  [{f['dim']}]" if f["dim"] else ""
            out.append(f"   ({f['sec']}) {f['label']} = {f['val']}  ({f['period']}){d}")
        out.append("")
    rp = config.OUTPUT_DIR / "_notes_loaded_sample.txt"
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    rp.write_text("\n".join(out), encoding="utf-8")
    print("SAMPLE DUMP →", rp)


if __name__ == "__main__":
    main()
