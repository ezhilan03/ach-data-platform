# First run on the build machine

This repository was authored in a sandbox with no package-registry access, so
three layers have never executed: the dbt SQL against DuckDB, the DAG files
against a real Airflow, and the Docker stack. Everything that could be checked
without those was (unit tests, the banking-day closed form, the dashboard JSON
layout, syntax across every file), but "it compiles" is not "it is correct".

Work through this once, in order. Delete this file afterwards.

## 1. Install and lock

```bash
cd ~/projects/ach-data-platform
make install
make lock            # commit the resulting uv.lock
```

## 2. Run the pipeline locally, before any containers

```bash
make backfill        # ~1-2 min: 60 days of Parquet + the ground-truth plan
make dbt             # first execution of the SQL — expect to fix things here
```

Most likely places the SQL needs a nudge, in rough order of probability:

- `banking_days_between` uses `//` for integer division and `dayofweek()`.
  If DuckDB complains, the arithmetic is pinned by `tests/test_banking_days.py`
  — port that closed form, do not invent a new one.
- The `range between interval 59 days preceding` window in
  `fct_return_rate_60d` needs an `ORDER BY` on a date/timestamp column;
  DuckDB may want `INTERVAL 59 DAY` spelled differently.
- The `external_location` on the `raw` source renders `{name}` per table.
  Check that `ACH_LAKE_ROOT` resolves — run `dbt compile` and read
  `target/compiled/` if a source fails to resolve.

## 3. Verify the marts against the reference implementation

```bash
make crosscheck
```

This is the important one. It recomputes `fct_originator_daily` in pandas from
the generator's own output and compares 17 columns row by row. If the SQL and
the reference disagree, **one of them is wrong** — do not assume it is the
reference. Likely suspects are the trailing-window frame (must exclude the
current row) and the settlement-lag units.

## 4. Measure, then update the README

```bash
make detect
make triage-rules    # rules only, no model needed
make eval
make check           # enforces eval/thresholds.json
python scripts/eval_table.py
```

The README's results table currently carries numbers measured through the
**pandas reference implementation**, because the SQL had never run. If step 3
passes, they will match. If `eval_table.py` prints anything different, paste
its output into the README — the table is generated, not typed.

Then ratchet `eval/thresholds.json` up to sit just under the real numbers.

## 5. Add the model

```bash
ollama serve &
ollama pull qwen3:4b
make triage          # rules + LLM
make eval
python scripts/eval_table.py   # now includes the LLM row
```

The interesting question is not whether the LLM beats 1.00 on classification —
it cannot, and the rules baseline is handed the answer. It is
`no_incident_rate_on_fp_clusters`: on the 2 false-positive clusters, does the
model correctly say "this is noise" where the lookup table structurally cannot?
That number is the actual case for the LLM being in the pipeline, and it is in
`data/runs/eval.json` under `triage.llm`.

## 6. Bring up the stack

```bash
make build
make parse-dags      # DAG import check inside the image — do this before `up`
make up
make airflow-password
```

Watch for:

- `@task.bash` returning a command string is Airflow 3's form; if the task
  fails on the decorator, check the `airflow.sdk` import.
- `AirflowSkipException` on weekends should show as *skipped*, not failed.
- The Simple auth manager writes its password file on first apiserver start.
  If it never appears, set `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=true`
  in `.env` to bypass login for a local demo.
- Ollama from inside the container reaches the host at
  `host.docker.internal:11434` (already set in compose via `extra_hosts`).

Unpause `ach_ingest`. Catchup replays the history; `ach_transform` and
`ach_eval` fire on their assets.

## 7. Check the dashboards, then screenshot them

Grafana at `localhost:3000`, folder **ACH Platform**. Two dashboards. The panel
queries were written against the metric names in
`monitoring/statsd/mapping.yml` but have never been run against live data —
expect one or two "No data" panels where a metric name needs adjusting. The
fix is always in the mapping file or the panel `expr`, never in the pipeline.

Screenshots of both dashboards belong in the README (`docs/img/`). A monitoring
project without a picture of the monitoring is asking a reader to take it on
faith.

## 8. Push

```bash
git init && git add -A
git commit -m "ACH data platform: Airflow 3 + dbt/DuckDB + Prometheus/Grafana"
gh repo create ezhilan03/ach-data-platform --public --source=. --push
```

CI runs on the first push. The `pipeline` job reproduces the numbers in the
README on a clean machine, which is the strongest single signal in the repo —
worth confirming it goes green before the portfolio card links to it.
