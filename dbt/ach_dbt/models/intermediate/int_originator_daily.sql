-- Originator x effective-date grain. Returns are attributed to the day the
-- entry was *originated*, not the day the return arrived: that is how return
-- rates are defined (returns / entries originated), and it's what makes a
-- spike land on the day the bad file went out.
--
-- Consequence worth knowing: the most recent days are under-counted until the
-- return window closes (unauthorized returns can arrive ~10 banking days
-- later). The detector accounts for this with a maturity cutoff.
with base as (
    select * from {{ ref('int_entries_with_returns') }}
)

select
    company_id,
    effective_entry_date,
    count(*)                                                        as entry_count,
    count(distinct file_id)                                         as file_count,
    sum(case when is_debit then 1 else 0 end)                       as debit_count,
    sum(case when is_debit then 0 else 1 end)                       as credit_count,
    sum(amount_cents)                                               as amount_cents,
    sum(case when is_debit then amount_cents else 0 end)            as debit_amount_cents,
    sum(case when is_debit then 0 else amount_cents end)            as credit_amount_cents,
    sum(case when is_returned then 1 else 0 end)                    as return_count,
    sum(case when return_class = 'unauthorized' then 1 else 0 end)  as unauthorized_return_count,
    sum(case when return_class = 'administrative' then 1 else 0 end) as admin_return_count,
    sum(case when return_class = 'nsf' then 1 else 0 end)           as nsf_return_count,
    sum(case when return_class = 'other' then 1 else 0 end)         as other_return_count,
    sum(case when is_duplicate_submission then 1 else 0 end)        as duplicate_entry_count,
    avg(settlement_lag_days)                                        as avg_settlement_lag_days,
    max(settlement_lag_days)                                        as max_settlement_lag_days
from base
group by 1, 2
