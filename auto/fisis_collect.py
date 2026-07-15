"""FISIS 금융통계(금융감독원 openapi) → Supabase fisis_stats 풀백필.

fin_stats(=data.go.kr, fnco_cd==FISIS finance_cd 확인됨)로 대상회사·corp_code·sector 확보 →
권역별 전 통계표를 40분기 청크로 statisticsInfoSearch → (회사·통계표·분기)당 JSONB 1행 upsert.
resume: 완료한 (finance_cd,list_no)를 output/fisis_prog.txt에 기록 → 다음 실행 skip.

키: FISIS_API_KEY[, FISIS_API_KEY2, FISIS_API_KEY3 …] 또는 FISIS_API_KEYS(콤마구분).
    여러 개면 키당 --per-key-calls(기본 9900) 소진 시 다음 키로 자동 전환(총예산=키수×9900).

사용:
  python fisis_collect.py --dry                       # 검증(업서트X)
  python fisis_collect.py                              # 적재(로드된 키 전부 사용)
  python fisis_collect.py --per-key-calls 9900        # 키당 상한 지정
  python fisis_collect.py --sectors bank,securities   # 권역 한정
"""
from __future__ import annotations
import os, sys, re, io, json, time, zipfile, argparse, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
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
BASE = "http://fisis.fss.or.kr/openapi"
_H = {"apikey": config.SUPABASE_SERVICE_ROLE_KEY,
      "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}"}

SEC_DIV = {"bank": "A", "savings": "E", "holding": "L", "securities": "F",
           "lifeins": "H", "nonlifeins": "I", "card": "C", "install": "T",
           "lease": "K", "newtech": "N", "retrust": "M", "assetmgmt": "G", "advisory": "X"}
CHUNKS = [("201912", "202606"), ("200912", "201909"), ("199912", "200909")]
# 반기(H)·연간(Y)도 FISIS는 조회구간을 40분기(=120개월)로 제한 → H는 ≤38분기 3구간으로 분할
HCHUNKS = [("201806", "202606"), ("200906", "201712"), ("199912", "200906")]
_ID = {"base_month", "finance_cd", "finance_nm", "account_cd", "account_nm"}
PROG = config.OUTPUT_DIR / "fisis_prog.txt"


def log(m):
    print(f"[{datetime.now(KST):%m-%d %H:%M:%S}] {m}", flush=True)


# ── 신규 금융사 판별·corp_code 매칭 (fisis_refresh 등 공용) ──────────────
_CC_F = config.OUTPUT_DIR / "corpcode.json"
_CC_IDX = None


def clean_nm(s):
    s = re.sub(r"주식회사|㈜|\(주\)|\[[^\]]*\]|\([^)]*\)", "", s or "")
    return re.sub(r"[\s.,·]", "", s)


def is_dead(nm):
    """폐지·구법인 표기 → 수집 대상 제외."""
    return "폐" in (nm or "") or "(구)" in (nm or "")


def _cc_entries():
    """corpcode.json 캐시. 없으면 OpenDART corpCode.xml(ZIP) 1콜로 생성 —
    output/ 은 gitignore라 서버엔 없음 → 서버에서 자가복구되게 함."""
    if _CC_F.exists():
        return json.loads(_CC_F.read_text(encoding="utf-8"))
    log("corpcode.json 없음 → OpenDART corpCode.xml 다운로드(1콜)")
    r = requests.get(config.EP_CORP_CODE, params={"crtfc_key": config.DART_API_KEY}, timeout=180)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]).decode("utf-8"))
    entries = [{"cc": e.findtext("corp_code"), "nm": e.findtext("corp_name"),
                "sk": (e.findtext("stock_code") or "").strip()} for e in root.iter("list")]
    _CC_F.parent.mkdir(parents=True, exist_ok=True)
    _CC_F.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    log(f"corpCode 캐시 생성: {len(entries):,}개")
    return entries


# 영문약칭 ↔ 한글음차 (FISIS "KB증권" ↔ DART "케이비증권", FISIS "에이치비캐피탈" ↔ DART "HB캐피탈").
# 이 부류가 수동패치의 대부분이었음(KB/KG/DB증권·캐피탈·자산운용, BNK, HB) → 양방향 자동화.
_EN2KO = {"A": "에이", "B": "비", "C": "씨", "D": "디", "E": "이", "F": "에프", "G": "지",
          "H": "에이치", "I": "아이", "J": "제이", "K": "케이", "L": "엘", "M": "엠", "N": "엔",
          "O": "오", "P": "피", "Q": "큐", "R": "알", "S": "에스", "T": "티", "U": "유",
          "V": "브이", "W": "더블유", "X": "엑스", "Y": "와이", "Z": "제트"}


def translit_nm(s):
    """선두 영문 약칭 런(1~4자)만 한글 음차로. 'KB증권'→'케이비증권'. 긴 영문단어는 그대로."""
    if not s:
        return s
    return re.sub(r"[A-Za-z]{1,4}",
                  lambda m: "".join(_EN2KO.get(ch, ch) for ch in m.group().upper()), s)


def resolve_corp(fisis_nm):
    """FISIS 회사명 → OpenDART corp_code. 없으면 None.

    단계: ① exact  ② 음차 정규화 exact(KB↔케이비 양방향)  ③ DART가 FISIS로 시작(FISIS=축약형)
          ④ FISIS가 DART로 시작(FISIS=확장형, '삼성생명보험'→DART '삼성생명') — 가장 긴 DART명 채택.
    ⚠️ 외국계 '○○ 서울지점/한국지점'은 DART 미등록이라 None이 정상.
    """
    global _CC_IDX
    if _CC_IDX is None:
        _CC_IDX = {}
        for e in _cc_entries():
            cn = clean_nm(e["nm"])
            _CC_IDX.setdefault(cn, []).append(e)
            tn = translit_nm(cn)                       # DART 영문약칭도 음차 키로 색인
            if tn != cn:
                _CC_IDX.setdefault(tn, []).append(e)
    c = clean_nm(fisis_nm)
    for key in (c, translit_nm(c)):                    # ①②
        if key in _CC_IDX:
            return _CC_IDX[key][0]["cc"]
    if len(c) < 4:
        return None
    # 외국계 '○○ 서울지점/한국지점'은 DART 미등록이 정상 → 퍼지매칭 금지(오탐 방지).
    # (정확/음차 일치는 위에서 이미 허용 — 혹시 등록돼 있으면 잡힘)
    if "지점" in c:
        return None
    cands = [e for cn, es in _CC_IDX.items() if cn.startswith(c) for e in es]   # ③
    if cands:
        cands.sort(key=lambda e: (0 if e["sk"] else 1, len(e["nm"])))
        return cands[0]["cc"]
    # ④ FISIS가 확장형('삼성생명보험'→DART '삼성생명'). 남는 꼬리가 짧을 때만(≤3자)
    #    허용 — 길게 남으면 '스타인터내셔널…한국지점'류 오탐이 된다.
    best = None
    for cn, es in _CC_IDX.items():
        if len(cn) >= 4 and c.startswith(cn) and len(c) - len(cn) <= 3:
            for e in es:
                if best is None or len(clean_nm(e["nm"])) > len(clean_nm(best["nm"])):
                    best = e
    return best["cc"] if best else None


def load_keys():
    ks = []
    for n in ("FISIS_API_KEY", "FISIS_API_KEY2", "FISIS_API_KEY3", "FISIS_API_KEY4", "FISIS_API_KEY5"):
        v = os.getenv(n, "").strip()
        if v:
            ks.append(v)
    for x in os.getenv("FISIS_API_KEYS", "").split(","):
        if x.strip():
            ks.append(x.strip())
    seen, out = set(), []
    for k in ks:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# 키 로테이션 상태 (키당 cap 소진 시 다음 키로 전환; 전부 소진이면 exhausted)
_K = {"keys": load_keys(), "i": 0, "used": 0, "total": 0, "cap": 9900}

# 키 사용상태를 파일로 공유 → 별도 프로세스로 나눠 돌려도 키 로테이션·일일한도가 이어짐(당일만 유효).
import json as _json
_KSTATE_F = config.OUTPUT_DIR / "fisis_keystate.json"


def _kst_today():
    return datetime.now(KST).strftime("%Y%m%d")


def _kst_load():
    try:
        d = _json.loads(_KSTATE_F.read_text(encoding="utf-8"))
        if d.get("date") == _kst_today():
            _K["i"], _K["used"] = int(d.get("i", 0)), int(d.get("used", 0))
    except Exception:
        pass


def _kst_save():
    try:
        config.OUTPUT_DIR.mkdir(exist_ok=True)
        _KSTATE_F.write_text(_json.dumps({"date": _kst_today(), "i": _K["i"], "used": _K["used"]}), encoding="utf-8")
    except Exception:
        pass


_kst_load()


def keys_exhausted():
    return _K["i"] >= len(_K["keys"])


def fisis(op, **p):
    if keys_exhausted():
        return {"__exhausted__": True}
    p = {"lang": "kr", "auth": _K["keys"][_K["i"]], **p}
    url = f"{BASE}/{op}.json?" + urllib.parse.urlencode(p)
    res = {}
    for a in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                res = json.loads(r.read().decode("utf-8", "replace")).get("result", {})
            break
        except Exception as e:
            if a == 3:
                log(f"  ! fisis 실패 {op} {p.get('financeCd','')}/{p.get('listNo','')}: {repr(e)[:80]}")
            else:
                time.sleep(2 ** a)
    _K["used"] += 1
    _K["total"] += 1
    if _K["used"] >= _K["cap"]:
        _K["i"] += 1
        _K["used"] = 0
        if not keys_exhausted():
            log(f"  키#{_K['i']} 예산({_K['cap']}콜) 소진 → 키#{_K['i']+1}로 전환")
    _kst_save()
    return res


def load_targets(sectors):
    base = config.SUPABASE_URL + "/rest/v1/fin_stats"
    tg = {}
    for sec in sectors:
        off = 0
        while True:
            r = requests.get(base, headers={**_H, "Range": f"{off}-{off + 999}"},
                             params={"select": "fnco_cd,fnco_nm,corp_code", "sector": f"eq.{sec}"}, timeout=60)
            b = r.json() if r.status_code in (200, 206) else []
            for x in b:
                cd = x.get("fnco_cd")
                if cd and cd not in tg:
                    tg[cd] = {"corp_code": x.get("corp_code"), "sector": sec, "nm": x.get("fnco_nm")}
            if len(b) < 1000:
                break
            off += 1000
    return tg


def load_prog():
    if PROG.exists():
        return set(l.strip() for l in PROG.read_text(encoding="utf-8").splitlines() if l.strip())
    return set()


def mark_prog(fc, listno):
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    with PROG.open("a", encoding="utf-8") as f:
        f.write(f"{fc}|{listno}\n")


def parse(res):
    out = {}
    for it in res.get("list") or []:
        pm, ac = it.get("base_month"), it.get("account_cd")
        if not (pm and ac):
            continue
        vals = {k: v for k, v in it.items() if k not in _ID}
        out.setdefault(pm, {})[ac] = {"nm": it.get("account_nm"), **vals}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sectors", default="all")
    ap.add_argument("--per-key-calls", type=int, default=9900)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    _K["cap"] = a.per_key_calls
    if not _K["keys"]:
        raise SystemExit("FISIS 키 미설정 — auto/.env 의 FISIS_API_KEY[/2/3] 확인")
    log(f"FISIS 키 {len(_K['keys'])}개 로드 · 키당 {_K['cap']}콜 → 총예산 ~{len(_K['keys'])*_K['cap']:,}")

    sectors = list(SEC_DIV) if a.sectors == "all" else [s.strip() for s in a.sectors.split(",")]
    targets = load_targets(sectors)
    log(f"대상 회사 {len(targets)}사")
    prog = set() if a.dry else load_prog()
    if prog:
        log(f"이미 완료 {len(prog)}건 → skip")

    built = rows_up = 0
    stopped = False
    for sec in sectors:
        if not a.dry and keys_exhausted():
            stopped = True
            break
        div = SEC_DIV[sec]
        our = {cd for cd, m in targets.items() if m["sector"] == sec}
        if not our:
            continue
        tl = fisis("statisticsListSearch", lrgDiv=div).get("list") or []
        tables = [(t["list_no"], t.get("list_nm")) for t in tl if t.get("list_no")]
        cl = fisis("companySearch", partDiv=div).get("list") or []
        fnm = {c["finance_cd"]: c.get("finance_nm") for c in cl}
        log(f"[{sec}/{div}] 대상 {len(our)}사 × 통계표 {len(tables)}개")
        if a.dry:
            continue
        for fc in sorted(our):
            meta = targets[fc]
            for ln, lnm in tables:
                if f"{fc}|{ln}" in prog:
                    continue
                if keys_exhausted():
                    stopped = True
                    break
                agg = {}
                for s, e in CHUNKS:
                    agg.update(parse(fisis("statisticsInfoSearch", financeCd=fc, listNo=ln,
                                            term="Q", startBaseMm=s, endBaseMm=e)))
                    time.sleep(config.REQUEST_SLEEP)
                buf = [{"finance_cd": fc, "list_no": ln, "period_ym": pm, "corp_code": meta["corp_code"],
                        "finance_nm": fnm.get(fc) or meta["nm"], "sector": sec, "div": div,
                        "list_nm": lnm, "data": accs} for pm, accs in agg.items()]
                if buf:
                    sb.upsert("fisis_stats", buf, "finance_cd,list_no,period_ym")
                    rows_up += len(buf)
                built += 1
                mark_prog(fc, ln)
                if built % 100 == 0:
                    log(f"  진행: 통계표 {built} · 콜 {_K['total']} · 적재 {rows_up:,}행")
            if stopped:
                break

    log(f"=== {'DRY' if a.dry else '수집'} 종료 · 콜 {_K['total']} · 통계표 {built} · 적재 {rows_up:,}행"
        + (" · 키예산 소진(다음 실행 resume)" if stopped else " · 완료"))
    print(f"CALLS={_K['total']}", flush=True)


if __name__ == "__main__":
    main()
