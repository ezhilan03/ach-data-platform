{#
  Number of banking days (Mon-Fri) in the half-open interval (start_date, end_date].
  Federal holidays are deliberately ignored, matching the generator.

  Closed form, verified exhaustively against a day-by-day count in
  tests/test_banking_days.py: subtract the Saturdays and Sundays that fall in
  the interval from the calendar-day difference. DuckDB's dayofweek() is
  0 = Sunday .. 6 = Saturday.

  Why this matters: a Friday entry that settles Monday has a 3-calendar-day
  lag but a 1-banking-day lag. Measuring calendar days made every Friday look
  like a settlement-delay incident (first eval run: 160 false positives).
#}
{% macro banking_days_between(start_date, end_date) -%}
(
    date_diff('day', {{ start_date }}, {{ end_date }})
    - ((date_diff('day', {{ start_date }}, {{ end_date }}) + 7
        - (case when dayofweek({{ start_date }}) = 6 then 7 else 6 - dayofweek({{ start_date }}) end)) // 7)
    - ((date_diff('day', {{ start_date }}, {{ end_date }}) + 7
        - (case when dayofweek({{ start_date }}) = 0 then 7 else 7 - dayofweek({{ start_date }}) end)) // 7)
)
{%- endmacro %}
