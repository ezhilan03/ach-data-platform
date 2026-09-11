# ADR-0002: One transform run at a time

**Status:** accepted · 2026-09-11

## Context

DuckDB allows many readers or one writer against a database file, not both.
The ingest DAG runs with `catchup=True` over 60 days and fans out to four
concurrent runs; each one emits the `ach_raw_daily` asset, which triggers
`ach_transform`. Without a constraint, several `dbt build` processes would run
against one `.duckdb` file at once.

## Decision

`ach_transform` and `ach_eval` set `max_active_runs=1`. Ingest keeps
`max_active_runs=4` — it only writes Parquet, and every write is atomic.
Everything that reads the warehouse outside dbt (the detector, the triage
evidence builder, the cross-check) opens it with `read_only=True`.

## Why

The failure mode without this is not a clean error. It is an intermittent lock
conflict during a catchup, which looks like flakiness, gets retried, and
eventually produces a half-built warehouse that still passes its tests because
the tests run against whatever did get built.

Serialising transform costs nothing here: each run rebuilds the marts over the
whole lake anyway, so N queued runs and one run produce the same end state.
During a catchup, ingest fans out and transform drains behind it — which is the
correct shape for a batch platform, not a limitation being worked around.

## Consequences

- Transform throughput is bounded by one process. Irrelevant at this data size;
  the first thing to change if the lake grew.
- Moving to a multi-writer engine (Postgres, BigQuery) would let this be
  relaxed, and is the natural trigger for revisiting ADR-0001.
- `read_only=True` on every non-dbt connection is load-bearing, not defensive:
  a read-write connection from the detector would block dbt for the length of
  the detector's run.
