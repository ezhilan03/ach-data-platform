{#
  Generic column test: a rate is NULL (no denominator) or within [0, 1].
  A rate > 1 means the join fanned out somewhere upstream.
#}
{% test rate_between_zero_and_one(model, column_name) %}
    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} is not null
      and ({{ column_name }} < 0 or {{ column_name }} > 1)
{% endtest %}
