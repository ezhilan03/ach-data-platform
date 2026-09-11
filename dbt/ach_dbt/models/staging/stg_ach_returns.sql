-- Returns carry the class the generator assigned, but we re-derive it from the
-- public R-code mapping so the warehouse never trusts an upstream label it can
-- compute itself. (The raw column is kept for a data test that the two agree.)
with source as (
    select * from {{ source('raw', 'ach_returns') }}
)

select
    return_id,
    original_trace_number,
    original_entry_id,
    company_id,
    return_code,
    return_class                                        as return_class_raw,
    case
        when return_code in ('R05', 'R07', 'R10', 'R11', 'R29') then 'unauthorized'
        when return_code in ('R02', 'R03', 'R04')               then 'administrative'
        when return_code in ('R01', 'R09')                      then 'nsf'
        else 'other'
    end                                                 as return_class,
    amount_cents,
    cast(original_effective_date as date)               as original_effective_date,
    cast(return_date as date)                           as return_date,
    date_diff('day', cast(original_effective_date as date), cast(return_date as date))
                                                        as days_to_return,
    cast(dt as date)                                    as partition_date
from source
