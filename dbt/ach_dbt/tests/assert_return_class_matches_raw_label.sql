-- Singular test: the return class we derive from the R-code must agree with
-- the class the upstream file carried. Disagreement means either the mapping
-- in stg_ach_returns drifted from the public rules or the source is mislabeled.
select return_id, return_code, return_class, return_class_raw
from {{ ref('stg_ach_returns') }}
where return_class <> return_class_raw
