-- DartData 전체재무제표(전 계정) 테이블. Supabase SQL Editor 에서 1회 실행.
-- stmt = {"CFS":[{sj,id,nm,v,pv,ppv,o}, ...], "OFS":[...]}  (전 계정 line item)
create table if not exists financials_full (
  corp_code  text not null,
  corp_name  text,
  stock      text,
  year       text not null,
  reprt      text not null,
  reprt_nm   text,
  stlm_dt    text,
  rcept_no   text,
  currency   text default 'KRW',
  n_cfs      int  default 0,
  n_ofs      int  default 0,
  stmt       jsonb not null,
  updated_at timestamptz default now(),
  primary key (corp_code, year, reprt)
);

create index if not exists idx_ff_year on financials_full (year);
create index if not exists idx_ff_corp on financials_full (corp_code);

alter table financials_full enable row level security;  -- 정책 없음 = service_role 전용

-- updated_at 자동 갱신(업서트-UPDATE도 반영). set_updated_at()은 기존 함수 재사용.
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_ff_updated on financials_full;
create trigger trg_ff_updated before insert or update on financials_full
  for each row execute function set_updated_at();
