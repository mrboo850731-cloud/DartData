-- fisis_stats에 실제 존재하는 (회사 × 통계표) 쌍의 정본 목록.
-- fisis_refresh.py 가 이 뷰를 읽어 "DB에 있는 것은 전부" 갱신 대상으로 삼는다.
-- (기간 샘플링 방식은 최신치가 오래된 쌍을 누락시켜 영구 미갱신 위험 → 이 뷰로 대체)
-- Supabase SQL Editor 에서 1회 실행.

create or replace view fisis_pairs as
select
  finance_cd,
  list_no,
  max(corp_code)  as corp_code,
  max(finance_nm) as finance_nm,
  max(sector)     as sector,
  max(div)        as div,
  max(list_nm)    as list_nm,
  max(period_ym)  as last_period,   -- 이 쌍의 최신 보유 기간
  count(*)        as periods        -- 보유 기간 수
from fisis_stats
group by finance_cd, list_no;

-- 뷰는 RLS를 기반 테이블에서 상속(fisis_stats = RLS on·정책없음 = service_role 전용).
-- 확인:  select count(*) from fisis_pairs;
