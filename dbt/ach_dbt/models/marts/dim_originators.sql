-- Originator dimension with lifetime activity rolled up. Small, but it is what
-- a BI tool joins to, and it gives the triage prompt the industry context.
with originators as (
    select * from {{ ref('stg_originators') }}
),

activity as (
    select
        company_id,
        min(effective_entry_date)                                  as first_effective_date,
        max(effective_entry_date)                                  as last_effective_date,
        sum(entry_count)                                           as lifetime_entries,
        sum(return_count)                                          as lifetime_returns
    from {{ ref('int_originator_daily') }}
    group by 1
)

select
    o.company_id,
    o.company_name,
    o.industry,
    o.primary_sec_code,
    o.odfi_routing,
    o.base_daily_volume,
    a.first_effective_date,
    a.last_effective_date,
    coalesce(a.lifetime_entries, 0)                                as lifetime_entries,
    coalesce(a.lifetime_returns, 0)                                as lifetime_returns,
    coalesce(a.lifetime_returns, 0) * 1.0 / nullif(a.lifetime_entries, 0) as lifetime_return_rate
from originators o
left join activity a using (company_id)
