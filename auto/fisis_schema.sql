-- FISIS 금융통계(금융감독원 fisis.fss.or.kr/openapi) 적재 — fin_stats(data.go.kr)와 별개(비파괴).
-- 검증: data.go.kr fnco_cd == FISIS finance_cd → fin_stats로 corp_code·sector 매핑(이름매칭 불필요).
-- (회사·통계표·분기)당 JSONB 1행. data = { account_cd: {nm, a(값), b(구성비/보조), ...} }
-- Supabase SQL editor에서 1회 실행. (PostgREST로는 DDL 불가)

create table if not exists fisis_stats (
  finance_cd  text        not null,   -- FISIS 금융회사코드 (= data.go.kr fnco_cd)
  list_no     text        not null,   -- FISIS 통계표코드 (예: SA003=요약재무상태표(자산-은행계정))
  period_ym   text        not null,   -- 기준월 YYYYMM (분기: 03/06/09/12)
  corp_code   text,                   -- fin_stats 매핑(OpenDART) — Disclo 조인키(미매칭이면 null)
  finance_nm  text,                   -- 금융회사명(FISIS 현재명)
  sector      text,                   -- bank/securities/lifeins/... (fin_stats 정렬)
  div         text,                   -- FISIS 권역코드 (A/F/H/...)
  list_nm     text,                   -- 통계표명
  data        jsonb       not null,   -- { account_cd: {nm, a, b, ...} }
  ingested_at timestamptz not null default now(),
  primary key (finance_cd, list_no, period_ym)
);

create index if not exists fisis_corp_idx   on fisis_stats (corp_code, period_ym);
create index if not exists fisis_sector_idx on fisis_stats (sector, list_no, period_ym);
create index if not exists fisis_div_idx    on fisis_stats (div, period_ym);

alter table fisis_stats enable row level security;
