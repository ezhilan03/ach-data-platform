-- Typed, renamed view over raw entries. Derives the two flags every downstream
-- model needs (debit/credit, account type) from the NACHA transaction code so
-- that logic lives in exactly one place.
with source as (
    select * from {{ source('raw', 'ach_entries') }}
)

select
    entry_id,
    file_id,
    batch_id,
    trace_number,
    company_id,
    sec_code,
    transaction_code,
    amount_cents,
    amount_cents / 100.0                                as amount_usd,
    cast(effective_entry_date as date)                  as effective_entry_date,
    cast(settlement_date as date)                       as settlement_date,
    -- Banking days, not calendar days: Friday -> Monday is a lag of 1.
    {{ banking_days_between('cast(effective_entry_date as date)', 'cast(settlement_date as date)') }}
                                                        as settlement_lag_days,
    receiving_dfi_id,
    account_hash,
    transaction_code in ('27', '37')                    as is_debit,
    case when transaction_code in ('22', '27') then 'checking' else 'savings' end
                                                        as account_type,
    cast(dt as date)                                    as partition_date
from source
