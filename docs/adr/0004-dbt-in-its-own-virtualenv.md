# ADR-0004: dbt gets its own virtualenv inside the image

**Status:** accepted · 2026-09-11

## Context

The runtime image needs both Airflow 3.3 and dbt. Both are large applications
with opinions about Jinja2, click, protobuf, and packaging metadata, and both
pin them.

## Decision

One image, two Python environments. Airflow and the `ach_platform` package live
in the image's main environment. dbt lives at `/opt/dbt-venv` and is invoked by
absolute path through `$DBT_BIN`. The dbt task is a `@task.bash` calling
`$DBT_BIN build`.

## Why

Resolving Airflow and dbt into one environment is usually possible and
occasionally not, and the failure mode is asymmetric: a dependency conflict
does not break dbt in an obvious way, it breaks *the scheduler*, which is the
component whose job is to notice that things are broken. Isolating them makes
that class of failure structurally impossible rather than merely unlikely, and
it means either can be upgraded without re-resolving the other.

Invoking dbt as a subprocess rather than importing `dbtRunner` is the same
decision one layer down: the dbt invocation shows up in the Airflow task log as
the command a human would run, and the exit code means what it means.

The image runs `python -c "import ach_platform, duckdb, litellm"` and
`dbt --version` at build time, so a broken environment fails the build rather
than the first DAG run.

## Rejected alternatives

**Single environment.** Simpler until the day it isn't, with a bad failure mode.

**`@task.virtualenv` / `PythonVirtualenvOperator`.** Builds the environment at
task runtime — slower on every run, and it moves a dependency-resolution
failure from build time to 3am.

**Cosmos.** Real value on a large dbt project (model-level tasks, granular
retries and lineage in the Airflow UI). Here it would add a dependency and a
layer of indirection to run `dbt build` on five marts. Worth revisiting if the
project grows past a couple of dozen models.

## Consequences

- The image is larger by roughly the size of a dbt install.
- `DBT_BIN` must be set wherever the pipeline runs. Local runs fall back to
  plain `dbt` on `PATH` (`pipeline.dbt_command`).
- dbt's Python version is the image's Python version; a dbt release requiring a
  different one needs a base-image change.
