-- DS002 정기보고서 주요정보 테이블. Supabase SQL Editor 에서 1회 실행.
-- 회사·기간당 1행(JSONB). financials/financials_full/financials_notes 와 (corp_code,year,reprt) 조인.
-- data = { "alotMatter":[...], "hyslrSttus":[...], "hmvAuditIndvdlBySttusV2":{"g":[...]}, ... }  (항목별, 빈건 제외)
create table if not exists periodic_info (
  corp_code  text not null,
  corp_name  text,
  stock      text,
  year       text not null,
  reprt      text not null,
  reprt_nm   text,
  n_topics   int  default 0,   -- 데이터 있는 항목 수(30개 중)
  n_rows     int  default 0,
  data       jsonb not null,
  updated_at timestamptz default now(),
  primary key (corp_code, year, reprt)
);

create index if not exists idx_pi_year on periodic_info (year);
create index if not exists idx_pi_corp on periodic_info (corp_code);

alter table periodic_info enable row level security;  -- 정책 없음 = service_role 전용

create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_pi_updated on periodic_info;
create trigger trg_pi_updated before insert or update on periodic_info
  for each row execute function set_updated_at();
