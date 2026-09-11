# ADR-0003: Schedule on assets, not on cron offsets

**Status:** accepted · 2026-09-11

## Context

Three stages — ingest, transform, evaluate — need to run in order. The
traditional shape is three cron schedules staggered by an educated guess
(06:00, 06:30, 07:00), or one monolithic DAG containing everything.

## Decision

`ach_ingest` runs `@daily`. `ach_transform` is scheduled on the
`ach_raw_daily` asset. `ach_eval` is scheduled on the `ach_marts` asset.
Assets are declared once in `airflow/dags/assets.py` and attached as `outlets`
to the task that actually produces them.

## Why

**Staggered cron encodes a guess about duration as a production dependency.**
It is wrong the first time ingest takes 35 minutes instead of 20, and the
failure is silent: transform runs on yesterday's data and everything looks
green.

**The asset event is emitted by the task, so it carries a real guarantee.**
`ACH_RAW_DAILY` is attached to `land_and_validate`, which lands *and validates*
in one task. Splitting validation into a separate task would publish the asset
on a partition nobody had checked yet and let every consumer race the check. A
weekend, a skipped run, or a validation failure therefore stops the chain
correctly with no extra sensor logic.

**Three DAGs beat one.** Ingest is per-day and parallel; transform is
warehouse-wide and serial (ADR-0002); eval is a quality gate. They have
different concurrency needs, different retry semantics, and different owners in
any real team. One DAG would force the loosest of each.

**The lineage in the UI becomes the real dependency graph** rather than a
diagram someone maintains separately and forgets to update.

## Consequences

- Requires Airflow 2.4+ for datasets, and this uses the Airflow 3 `airflow.sdk`
  `Asset` API. Downgrading means rewriting the schedules.
- An asset that is never emitted means a DAG that never runs and never alerts.
  Mitigated by the `SchedulerHeartbeatMissing` and `DagImportErrors` alerts, and
  by the ingest freshness stat on the data-health dashboard.
