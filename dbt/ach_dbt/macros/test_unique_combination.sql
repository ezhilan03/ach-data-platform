{#
  Generic test: the listed columns are jointly unique. Written here instead of
  pulling dbt_utils so the project has zero package dependencies (one less
  thing for `dbt deps` to fetch in CI and in the container build).
#}
{% test unique_combination(model, columns) %}
    select {{ columns | join(', ') }}, count(*) as n
    from {{ model }}
    group by {{ columns | join(', ') }}
    having count(*) > 1
{% endtest %}
