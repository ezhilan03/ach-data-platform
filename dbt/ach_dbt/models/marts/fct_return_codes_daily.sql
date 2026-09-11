-- Return volume by arrival date and R-code. Feeds the "top return codes" panel
-- and the evidence packet the triage step builds for the LLM.
with returns as (
    select * from {{ ref('stg_ach_returns') }}
)

select
    return_date,
    company_id,
    return_code,
    return_class,
    count(*)                                                       as return_count,
    sum(amount_cents)                                              as returned_amount_cents,
    avg(days_to_return)                                            as avg_days_to_return
from returns
group by 1, 2, 3, 4
