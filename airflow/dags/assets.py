"""Assets are the contract between the three DAGs.

Airflow 3 schedules on assets rather than on cron-plus-hope: the transform DAG
does not run at 06:30 and trust that ingest finished, it runs when the raw
partition for a day actually exists. Same for evaluation. The result is that
the lineage in the UI is the real dependency graph, and a skipped ingest
(weekend, upstream outage) silently and correctly stops the chain instead of
producing a run over stale data.

Asset identifiers are scheme-less-friendly in Airflow 3, but a URI-ish name
keeps them self-describing in the asset view.
"""

from __future__ import annotations

from airflow.sdk import Asset

# Produced by ach_ingest once a day's raw Parquet is landed AND validated.
ACH_RAW_DAILY = Asset(
    uri="ach://lake/raw/daily",
    name="ach_raw_daily",
    extra={"layer": "raw", "format": "parquet", "partitioning": "hive (dt=)"},
)

# Produced by ach_transform once dbt build succeeds (models + tests) and the
# detector has run over the refreshed marts.
ACH_MARTS = Asset(
    uri="ach://warehouse/marts",
    name="ach_marts",
    extra={"layer": "marts", "engine": "duckdb"},
)
