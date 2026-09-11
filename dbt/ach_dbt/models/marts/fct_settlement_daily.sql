-- Network-level settlement view: what settled, when, by SEC code. This is the
-- "money movement" mart -- volume and dollars by settlement date, independent
-- of originator.
with entries as (
    select * from {{ ref('stg_ach_entries') }}
)

select
    settlement_date,
    sec_code,
    count(*)                                                       as entry_count,
    count(distinct company_id)                                     as originator_count,
    sum(case when is_debit then amount_cents else 0 end)           as debit_amount_cents,
    sum(case when is_debit then 0 else amount_cents end)           as credit_amount_cents,
    sum(amount_cents)                                              as total_amount_cents,
    sum(case when is_debit then amount_cents else -amount_cents end) as net_settlement_cents,
    avg(settlement_lag_days)                                       as avg_settlement_lag_days
from entries
group by 1, 2
