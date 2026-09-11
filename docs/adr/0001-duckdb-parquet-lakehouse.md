# ADR-0001: DuckDB over Parquet as the warehouse

**Status:** accepted · 2026-09-11

## Context

The platform needs a warehouse for dbt to model into. Three candidates:
Postgres in a container, DuckDB over a Parquet lake, or BigQuery.

## Decision

DuckDB as the engine, Hive-partitioned Parquet as the storage layer. dbt reads
the raw zone in place through `external_location` and materialises staging as
views, intermediate and marts as tables inside a single `.duckdb` file.

## Why

**Storage and compute are separated, which is the point of the pattern.** The
raw zone is open Parquet that any engine can read — Spark, Trino, BigQuery
external tables, pandas. Nothing is locked inside the query engine, and swapping
DuckDB for something else later is a profile change plus a re-run, not a
migration.

**It runs anywhere with no service.** No container to start in CI, no
credentials, no network. The whole pipeline — 60 days of history, five marts,
every dbt test, the detector and the eval — runs in one GitHub Actions job with
`uv sync` and nothing else. That is what makes the quality gate in CI viable;
a warehouse that needs a service and a secret usually ends up not being
exercised by CI at all.

**It matches the workload.** These are columnar scans and window functions over
tens of megabytes. That is exactly DuckDB's shape, and it is emphatically not
Postgres's — Postgres would need the same data loaded into it first, which adds
a copy, a service, and a schema migration story for no analytical benefit.

## Rejected alternatives

**Postgres in Docker Compose.** More familiar, and it matches the rest of the
portfolio's stack — which is precisely the argument against it here: the other
two projects already demonstrate Postgres. It would also mean loading the lake
into the database before modelling it, turning an open storage layer into an
internal one.

**BigQuery.** The strongest "real warehouse" signal and it would give Terraform
a more interesting target, but it needs a service account and has cost
exposure, and CI could not run `dbt build` without a secret. A portfolio
project whose tests cannot run on a fork is worse than one with a smaller
engine.

## Consequences

- DuckDB is single-writer. See ADR-0002.
- The `external_location` source config is dbt-duckdb-specific; porting to
  another adapter means replacing it with a real external-table definition.
- File-level concurrency is the platform's responsibility, which is why every
  Parquet write is atomic (temp file + `os.replace`).
