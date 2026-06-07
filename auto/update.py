"""DartData 상시 워커 증분 업데이트 → Supabase 직접 upsert (로컬 JSON 안 거침).

핵심 아이디어(중복 0, 항상 최신): 이벤트는 **period=연도** 키를 써서, 최근 활동이 있는
회사를 '그 회사의 현재연도 전체'로 다시 긁어 그 연도 묶음을 통째로 덮어쓴다. 몇 번을
돌려도 같은 키 → upsert 덮어쓰기라 누적 중복이 없다.

모드:
  python update.py events [--lookback 3]   # 15분 크론: 최근 N일 활동 회사의 현재연도 이벤트 갱신
  python update.py financials              # 일 1회 크론: 현재연도 재무 다중회사 갱신
  python update.py companies               # (선택) 현재연도 제출자 기업개황 갱신

events 의 period 정규화: 기존 백필 events(period="YYYYMMDD-YYYYMMDD")는 배포 시 SQL로
period→연도("YYYY")로 한 번 정규화해야 키가 일치한다(README/배포가이드 참조).
"""
from __future__ import annotations
import sys
import time
import argparse
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import requests
import config
import dart_api
import registry
import supabase_client as sb
import enumerate_filings as enm

KST = timezone(timedelta(hours=9))


def log(m):
    print(f"[{datetime.now(KST):%m-%d %H:%M:%S}] {m}", flush=True)


def _batch_upsert(table, rows, on_conflict, n=200):
    for i in range(0, len(rows), n):
        sb.upsert(table, rows[i:i + n], on_conflict)


# ── 이벤트 (15분) ──────────────────────────────────────────────
def run_events(lookback: int):
    today = datetime.now(KST).date()
    yr = str(today.year)
    bgn_y, end_y = f"{yr}0101", f"{yr}1231"
    bgn_recent = today - timedelta(days=lookback)

    # 1) 최근 활동(회사×항목) 열거 → 매칭
    work, meta = {}, {}
    for ty in ("B", "C", "D"):
        corps, _rpt, pairs, _f, _c = enm.enum_type(ty, bgn_recent, today)
        meta.update(corps)
        gname, matcher, table, needs_range = registry.GROUP[ty]
        for corp, report_nm in pairs:
            ep = matcher(report_nm)
            if not ep:
                continue
            if (corp, ep) not in work:
                label = next((l for (e, l, k) in table if e == ep), ep)
                work[(corp, ep)] = {"grp": gname, "label": label, "needs_range": needs_range}
    log(f"최근 {lookback}일 활동: {len(work)} (회사×항목)")

    # 2) 각 (회사, 항목)의 현재연도 전체 재수집 → events 행 (period=연도/snapshot)
    rows, calls = [], 0
    for (corp, ep), info in work.items():
        params = {"corp_code": corp}
        period = "snapshot"
        if info["needs_range"]:
            params.update(bgn_de=bgn_y, end_de=end_y)
            period = yr
        try:
            data = dart_api._get(f"{config.API_BASE}/{ep}.json", params)
        except Exception:
            continue
        calls += 1
        r_rows = data.get("list") or []
        groups = data.get("group") or []
        n = len(r_rows) + sum(len(g.get("list") or []) for g in groups)
        if not n:
            continue
        m = meta.get(corp, {})
        d = {k: v for k, v in (("rows", r_rows), ("groups", groups)) if v}
        rows.append({"endpoint": ep, "corp_code": corp, "period": period, "grp": info["grp"],
                     "label": info["label"], "corp_name": m.get("name", ""),
                     "stock": m.get("stock", ""), "cls": m.get("cls", ""), "n": n, "data": d})
        time.sleep(config.REQUEST_SLEEP)

    _batch_upsert("events", rows, "endpoint,corp_code,period")
    log(f"events upsert {len(rows)}건 (데이터콜 {calls})")

    # 성공 시 healthchecks.io ping (멈추면 이메일 알림). URL은 서버 .env 에만.
    if config.HEALTHCHECK_URL:
        try:
            requests.get(config.HEALTHCHECK_URL, timeout=10)
            log("healthcheck ping ✓")
        except Exception:
            pass


# ── 재무 (일 1회) ──────────────────────────────────────────────
def _parse_amt(s):
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t in ("", "-"):
        return None
    try:
        return int(t)
    except ValueError:
        try:
            return int(float(t))
        except ValueError:
            return None


def run_financials():
    today = datetime.now(KST).date()
    yr = str(today.year)
    # 올해 정기공시 제출자 = 유니버스 (상폐 포함, 신규 포함)
    corps, _rpt, _p, _f, _c = enm.enum_type("A", date(today.year, 1, 1), today)
    codes = sorted(corps)
    log(f"재무 갱신: {yr} 제출자 {len(codes):,}개사")
    calls = 0
    for reprt in config.REPRT_ALL:
        period = {}
        for i in range(0, len(codes), config.MULTI_BATCH):
            batch = codes[i:i + config.MULTI_BATCH]
            try:
                ar = dart_api.get_multi_account(batch, yr, reprt)
            except Exception:
                ar = []
            calls += 1
            for r in ar:
                c, fs, nm = r.get("corp_code"), r.get("fs_div"), r.get("account_nm")
                if not c or fs not in ("CFS", "OFS") or not nm:
                    continue
                rec = period.setdefault(c, _blank_fin(c, corps, yr, reprt))
                rec["acct"][fs].setdefault(nm, _parse_amt(r.get("thstrm_amount")))
                if not rec["rcept_no"]:
                    rec["rcept_no"] = r.get("rcept_no", "")
                cur = (r.get("currency") or "").strip()
                if (len(cur) == 3 and cur.isascii() and cur.isalpha()
                        and cur.upper() != "KRW" and (fs == "CFS" or rec["currency"] == "KRW")):
                    rec["currency"] = cur.upper()
            for cat, code in config.IDX_CL_CODE.items():       # 재무지표(2023 3분기~)
                try:
                    ir = dart_api.get_company_index(batch, yr, reprt, code)
                except Exception:
                    ir = []
                calls += 1
                for r in ir:
                    c, inm, val = r.get("corp_code"), r.get("idx_nm"), r.get("idx_val")
                    if not c or not inm or not (val or "").strip():
                        continue
                    rec = period.setdefault(c, _blank_fin(c, corps, yr, reprt))
                    sd = r.get("stlm_dt", "")
                    if sd:
                        rec["stlm_dt"] = sd
                    rec["idx"].setdefault(cat, {})[inm] = val
            time.sleep(config.REQUEST_SLEEP)
        rows = [r for r in period.values() if r["acct"]["CFS"] or r["acct"]["OFS"] or r["idx"]]
        _batch_upsert("financials", rows, "corp_code,year,reprt", 500)
        log(f"  {yr} {config.REPRT_NM[reprt]}: {len(rows)}건 upsert (누적 {calls}콜)")


def _blank_fin(corp, corps, yr, reprt):
    m = corps.get(corp, {})
    mm = config.REPRT_STLM.get(reprt, "12-31")
    return {"corp_code": corp, "corp_name": m.get("name", ""), "stock": m.get("stock", ""),
            "year": yr, "reprt": reprt, "reprt_nm": config.REPRT_NM.get(reprt, reprt),
            "stlm_dt": f"{yr}-{mm}", "rcept_no": "", "currency": "KRW",
            "acct": {"CFS": {}, "OFS": {}}, "idx": {}}


# ── 기업개황 (선택, 일 1회) ─────────────────────────────────────
def run_companies():
    today = datetime.now(KST).date()
    corps, _rpt, _p, _f, _c = enm.enum_type("A", date(today.year, 1, 1), today)
    rows, calls = [], 0
    for corp in sorted(corps):
        try:
            p = dart_api.get_company(corp)
        except Exception:
            p = {}
        calls += 1
        if p:
            rows.append({"corp_code": corp, "corp_name": p.get("corp_name"),
                         "corp_name_eng": p.get("corp_name_eng"), "stock_code": p.get("stock_code"),
                         "ceo_nm": p.get("ceo_nm"), "corp_cls": p.get("corp_cls"),
                         "induty_code": p.get("induty_code"), "est_dt": p.get("est_dt"),
                         "acc_mt": p.get("acc_mt"), "adres": p.get("adres"), "profile": p})
        time.sleep(config.REQUEST_SLEEP)
    _batch_upsert("companies", rows, "corp_code", 500)
    log(f"companies upsert {len(rows)}건 ({calls}콜)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["events", "financials", "companies"])
    ap.add_argument("--lookback", type=int, default=3, help="events: 최근 N일")
    args = ap.parse_args()
    t0 = time.time()
    log(f"=== update {args.mode} 시작 ===")
    if args.mode == "events":
        run_events(args.lookback)
    elif args.mode == "financials":
        run_financials()
    else:
        run_companies()
    log(f"=== update {args.mode} 완료 ({time.time()-t0:.0f}s) ===")


if __name__ == "__main__":
    main()
