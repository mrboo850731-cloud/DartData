-- DartData Supabase 스키마 (Pro 프로젝트의 SQL Editor 에 붙여넣고 Run)
-- 3개 테이블: financials(재무) · events(이벤트) · companies(기업개황)

-- ── 재무 (DS003) : 회사 × 연도 × 보고서 ──
create table if not exists public.financials (
  corp_code   text not null,
  year        text not null,
  reprt       text not null,
  corp_name   text,
  stock       text,
  reprt_nm    text,
  stlm_dt     text,
  rcept_no    text,
  currency    text default 'KRW',          -- 보고통화(외화 보존, 대안 B)
  acct        jsonb,                        -- 주요계정 {CFS:{계정:원}, OFS:{…}}
  idx         jsonb,                        -- 재무지표 {수익성:{지표:값}, …}
  updated_at  timestamptz not null default now(),
  primary key (corp_code, year, reprt)
);
create index if not exists idx_fin_period on public.financials (year, reprt);
create index if not exists idx_fin_stock  on public.financials (stock);

-- ── 이벤트 (DS004/005/006) : raw JSONB 랜딩 ──
-- period = "bgn-end"(주요사항·증권신고서, 연도범위) 또는 "snapshot"(지분공시, 연도무관)
create table if not exists public.events (
  endpoint    text not null,
  corp_code   text not null,
  period      text not null,
  grp         text,                         -- DS004/DS005/DS006
  label       text,                         -- 한글 항목명(유상증자 결정 등)
  corp_name   text,
  stock       text,
  cls         text,                         -- 시장구분 Y/K/N/E
  n           int,
  data        jsonb,                        -- {rows:[…]} 또는 {groups:[{title,list}]}
  updated_at  timestamptz not null default now(),
  primary key (endpoint, corp_code, period)
);
create index if not exists idx_ev_corp  on public.events (corp_code);
create index if not exists idx_ev_grp   on public.events (grp);
create index if not exists idx_ev_label on public.events (label);

-- ── 기업개황 (DS001) : 회사 프로필 ──
create table if not exists public.companies (
  corp_code      text primary key,
  corp_name      text,
  corp_name_eng  text,
  stock_code     text,
  ceo_nm         text,
  corp_cls       text,
  induty_code    text,
  est_dt         text,
  acc_mt         text,
  adres          text,
  profile        jsonb,                      -- 전체 원본 프로필
  updated_at     timestamptz not null default now()
);
create index if not exists idx_cmp_stock on public.companies (stock_code);

-- 보안: RLS 켜고 정책 없음 → service_role(서버 동기화)만 접근, 익명키 차단.
alter table public.financials enable row level security;
alter table public.events     enable row level security;
alter table public.companies  enable row level security;
