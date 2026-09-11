-- A return cannot arrive before the original entry settled.
select e.entry_id, e.settlement_date, e.return_date
from {{ ref('int_entries_with_returns') }} e
where e.is_returned
  and e.return_date <= e.settlement_date
