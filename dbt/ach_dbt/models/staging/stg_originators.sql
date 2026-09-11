select
    company_id,
    company_name,
    industry,
    sec_code                                            as primary_sec_code,
    odfi_routing,
    base_daily_volume
from {{ source('raw', 'originators') }}
