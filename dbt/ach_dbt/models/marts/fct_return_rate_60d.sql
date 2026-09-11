-- NACHA-style rolling 60-day return rates per originator, evaluated on every
-- date. This is the number an ODFI actually monitors: unauthorized > 0.5%,
-- administrative > 3.0%, overall > 15.0% over the preceding 60 days triggers
-- a network inquiry. Daily rates (fct_originator_daily) are for early warning;
-- this mart is for compliance reporting.
with daily as (
    select
        company_id,
        effective_entry_date,
        entry_count,
        debit_count,
        return_count,
        unauthorized_return_count,
        admin_return_count
    from {{ ref('int_originator_daily') }}
),

rolling as (
    select
        company_id,
        effective_entry_date                                           as as_of_date,
        sum(entry_count) over w                                        as entries_60d,
        sum(debit_count) over w                                        as debits_60d,
        sum(return_count) over w                                       as returns_60d,
        sum(unauthorized_return_count) over w                          as unauthorized_returns_60d,
        sum(admin_return_count) over w                                 as admin_returns_60d
    from daily
    window w as (
        partition by company_id
        order by effective_entry_date
        range between interval 59 days preceding and current row
    )
)

select
    company_id,
    as_of_date,
    entries_60d,
    debits_60d,
    returns_60d,
    unauthorized_returns_60d,
    admin_returns_60d,
    -- NACHA computes the unauthorized and administrative rates against debit
    -- entries; the overall rate against all entries.
    unauthorized_returns_60d * 1.0 / nullif(debits_60d, 0)            as unauthorized_return_rate_60d,
    admin_returns_60d * 1.0 / nullif(debits_60d, 0)                   as admin_return_rate_60d,
    returns_60d * 1.0 / nullif(entries_60d, 0)                        as overall_return_rate_60d,
    unauthorized_returns_60d * 1.0 / nullif(debits_60d, 0) > 0.005    as breaches_unauthorized_threshold,
    admin_returns_60d * 1.0 / nullif(debits_60d, 0) > 0.03            as breaches_admin_threshold,
    returns_60d * 1.0 / nullif(entries_60d, 0) > 0.15                 as breaches_overall_threshold
from rolling
