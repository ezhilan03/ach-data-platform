-- Entry grain, enriched with its return (if any). Left join: most entries never
-- return. Also flags duplicate submissions: the same originator debiting the
-- same account for the same amount on the same effective date more than once
-- is the signature of a file submitted twice.
with entries as (
    select * from {{ ref('stg_ach_entries') }}
),

returns as (
    select * from {{ ref('stg_ach_returns') }}
),

dup_keys as (
    select
        company_id,
        account_hash,
        amount_cents,
        effective_entry_date,
        count(*)                                        as submissions
    from entries
    group by 1, 2, 3, 4
)

select
    e.entry_id,
    e.file_id,
    e.trace_number,
    e.company_id,
    e.sec_code,
    e.transaction_code,
    e.is_debit,
    e.account_type,
    e.amount_cents,
    e.amount_usd,
    e.effective_entry_date,
    e.settlement_date,
    e.settlement_lag_days,
    e.account_hash,
    r.return_id,
    r.return_code,
    r.return_class,
    r.return_date,
    r.days_to_return,
    r.return_id is not null                             as is_returned,
    coalesce(d.submissions, 1) > 1                      as is_duplicate_submission
from entries e
left join returns r
    on r.original_entry_id = e.entry_id
left join dup_keys d
    on  d.company_id = e.company_id
    and d.account_hash = e.account_hash
    and d.amount_cents = e.amount_cents
    and d.effective_entry_date = e.effective_entry_date
