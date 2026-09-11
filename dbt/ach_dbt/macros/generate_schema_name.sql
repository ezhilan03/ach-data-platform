{#
  Default dbt behaviour is "<target_schema>_<custom_schema>" (e.g. main_marts).
  For a single-file DuckDB warehouse plain layer names read better in the
  catalog and in SELECTs from the detector: staging.stg_ach_entries, marts.fct_originator_daily.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
