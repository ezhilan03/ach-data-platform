# What still needs a human

Most of the bring-up is done and verified on a Linux machine with the real
toolchain (Python 3.12, duckdb 1.5.5, dbt-core 1.12.5, dbt-duckdb 1.11.0,
apache-airflow 3.3.1). What follows is what that environment could not reach.

Delete this file once it is empty.

## Verified already — no action needed

| Step | Result |
| --- | --- |
| `uv sync --extra dbt --extra dev` | clean |
| `ruff check` / `ruff format --check` | clean, 40 files |
| `pytest` | 36 passed |
| `python -m ach_platform backfill` | 181,635 entries · 1,009 files · 4,369 returns · 181 Parquet partitions |
| `dbt build` | PASS=57 (11 models, 46 tests), 1.8s, no deprecation warnings |
| `scripts/crosscheck_marts.py` | 17 columns agree across 1,008 originator-days |
| `detect` → `triage --no-llm` → `eval` | reproduces the README table exactly |
| `scripts/check_eval.py` | all floors met |
| `scripts/parse_dags.py` (real `DagBag`, Airflow 3.3.1) | 3 DAGs, 0 import errors |
| `tests/test_monitoring_contract.py` | 9 checks — metric names, labels, compose mounts, scrape config all resolve |
| secret / identity scan of the committed tree | clean: no credentials, no employer or vendor names, no personal paths |

Three bugs were found and fixed in the process; see the git log and
[ADR-0008](adr/0008-dag-files-are-thin-wiring.md).

## 1. The Docker stack — not run

No Docker daemon was available. Everything above ran against a local venv, so
the image build, the compose topology, and the services talking to each other
are still unverified.

```bash
make build
make parse-dags     # the same DAG check, but inside the image
make up
make airflow-password
```

Watch for:

- The image installs `ach_platform` from `src/`, and the DAGs now import
  `ach_platform.assets`. If the DAG processor reports import errors, the
  package did not install — check the `pip install /opt/ach` layer.
- `dbt` lives at `/opt/dbt-venv/bin/dbt` (`$DBT_BIN`), not on `PATH`.
- The Simple auth manager writes its password file on first apiserver start.
  If it never appears, set `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=true`
  in `.env` to bypass login for a local demo.
- Unpause `ach_ingest`; catchup replays the history. `ach_transform` and
  `ach_eval` fire on their assets.

## 2. The Grafana dashboards — never seen live

The panels have never rendered against real data, but the risk is now much
narrower than it was. `tests/test_monitoring_contract.py` checks statically
that every metric a panel queries is one the code actually emits, that every
label matcher and legend template references a declared label, and that the
compose mounts, scrape targets and datasource UIDs resolve. It found two
metrics being pushed on every run and charted nowhere
(`ach_dbt_model_execution_seconds`, `ach_raw_validation_warnings`); both now
have panels.

What that cannot check is the Airflow side: those metric names depend on how
`statsd_exporter` renders DogStatsD tags at runtime. If a panel on the
pipeline-health dashboard shows "No data", read `http://localhost:9102/metrics`
to see the names the exporter is actually producing, and fix
`monitoring/statsd/mapping.yml` or the panel `expr` — never the pipeline.

Screenshots of both dashboards belong in the README (`docs/img/`). A monitoring
project with no picture of the monitoring asks the reader to take it on faith.

## 3. LLM triage — never measured

No Ollama was reachable, so the triage step has only ever run as the rules
baseline. Every LLM figure in the README is still blank.

```bash
ollama serve &
ollama pull qwen3:4b
make triage
make eval
python scripts/eval_table.py    # now includes the LLM row — paste it into the README
```

The interesting number is not classification accuracy — the rules baseline
scores 1.00 there and is handed the answer. It is
`triage.llm.no_incident_rate_on_fp_clusters` in `data/runs/eval.json`: on the
two false-positive clusters, does the model say "this is noise" where the
lookup table structurally cannot? That is the case for the LLM being in the
pipeline at all, and if it comes back weak, ADR-0007 already says what to do.

## 4. Push

```bash
cd ~/projects/ach-data-platform
gh repo create ezhilan03/ach-data-platform --public --source=. --push
```

CI runs on first push. The `pipeline` job reproduces the README's numbers on a
clean machine, which is the strongest single signal in the repo — worth
confirming it goes green before the portfolio card links to it.

The `docker` job builds the image and runs the DAG parse inside it, which is
the check that caught the `sys.path` bug. If it passes, section 1 above is
largely settled too.
