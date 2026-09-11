with source as (
    select * from {{ source('raw', 'ach_files') }}
)

select
    file_id,
    company_id,
    cast(file_creation_date as date)                    as file_creation_date,
    file_sequence,
    batch_count,
    entry_count,
    total_debit_cents,
    total_credit_cents,
    cast(dt as date)                                    as partition_date
from source
