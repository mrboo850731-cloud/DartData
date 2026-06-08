-- DartData 재무제표 주석 테이블. Supabase SQL Editor 에서 1회 실행.
-- 회사·기간당 1행(JSONB). financials_full / financials 와 (corp_code, year, reprt) 로 조인.
-- notes = { "rdate":"20260331",
--           "narrative":[{sec,label,html}, ...],            -- 서술형 주석
--           "facts":[{sec,label,val,period,dim}, ...] }      -- 주석섹션 숫자 (본문 D2~D5 제외)
create table if not exists financials_notes (
  corp_code  text not null,
  year       text not null,
  reprt      text not null,
  rdate      text,
  n_narr     int  default 0,
  n_facts    int  default 0,
  n_sec      int  default 0,
  notes      jsonb not null,
  updated_at timestamptz default now(),
  primary key (corp_code, year, reprt)
);

create index if not exists idx_fn_year on financials_notes (year);
create index if not exists idx_fn_corp on financials_notes (corp_code);

alter table financials_notes enable row level security;  -- 정책 없음 = service_role 전용

create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_fn_updated on financials_notes;
create trigger trg_fn_updated before insert or update on financials_notes
  for each row execute function set_updated_at();
