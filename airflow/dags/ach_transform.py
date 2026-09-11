"""ach_transform -- model the lake with dbt, then look for anomalies and triage them.

Scheduled on the ACH_RAW_DAILY asset, so it runs when a day actually lands, not
on a clock. `max_active_runs=1` is load-bearing: DuckDB is a single-writer
engine and two concurrent `dbt build` processes against one warehouse file
would deadlock or corrupt it (ADR-0002). During a catchup this means transform
runs queue behind each other while ingest fans out -- which is the correct
shape, since each transform run rebuilds the marts over the whole lake anyway.

Task order and why:

    dbt_build ──┬── publish_dbt_metrics   (all_done: a failed test is exactly
                │                          the thing the dashboard must show)
                └── detect_anomalies ── triage_anomalies ── publish_marts
                                                              (emits ACH_MARTS)

A failing dbt test fails dbt_build, which stops the chain before the marts are
declared fresh. The detector never reads a table that failed its own tests.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import dag, task
from assets import ACH_MARTS, ACH_RAW_DAILY

from ach_platform.config import get_settings
from ach_platform.pipeline import detect as detect_impl
from ach_platform.pipeline import publish_dbt_metrics as publish_dbt_metrics_impl
from ach_platform.pipeline import triage as triage_impl

SETTINGS = get_settings()


@dag(
    dag_id="ach_transform",
    description="dbt build over the raw lake, then anomaly detection and LLM triage",
    schedule=[ACH_RAW_DAILY],
    start_date=pendulum.datetime(2026, 7, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,  # DuckDB is single-writer -- see ADR-0002
    tags=["ach", "dbt", "transform", "ai"],
    default_args={"retries": 1, "retry_delay": pendulum.duration(seconds=30)},
    doc_md=__doc__,
)
def ach_transform():
    @task.bash(retries=0)
    def dbt_build() -> str:
        """`dbt build` = run + test, interleaved, in dependency order.

        Not `dbt run && dbt test`: build stops a failing model's children from
        being built at all, which is the behaviour we want on a bad day.
        Retries are off -- a dbt test failure is a data problem, and running it
        again will fail again while making the logs harder to read.
        """
        return (
            "$DBT_BIN build "
            "--project-dir $DBT_PROJECT_DIR "
            "--profiles-dir $DBT_PROJECT_DIR "
            "--target prod"
        )

    @task(trigger_rule="all_done")
    def publish_dbt_metrics() -> dict:
        """Parse target/run_results.json and push model/test outcomes.

        all_done, not all_success: the run that most needs to show up on the
        dashboard is the one where a test just failed.
        """
        return publish_dbt_metrics_impl(SETTINGS)

    @task
    def detect_anomalies() -> dict:
        return detect_impl(SETTINGS)

    @task
    def triage_anomalies() -> dict:
        """Rules baseline always; the LLM on top of it, best-effort.

        This task does not fail when the model is unreachable. The rules
        triage is deterministic and always produces a verdict, so an Ollama
        that isn't running degrades the output rather than reddening the DAG.
        Failures are counted and surfaced in the eval, not swallowed.
        """
        result = triage_impl(SETTINGS, use_llm=True, annotate=True)
        print(f"triaged {result['clusters']} clusters; llm produced {result['llm_success']} schema-valid results")
        return result

    @task(outlets=[ACH_MARTS])
    def publish_marts(detection: dict, triage_result: dict) -> dict:
        """Declare the marts fresh. Nothing runs here -- reaching this task is
        the statement: dbt tests passed, detection ran, triage ran."""
        return {"anomalies": detection["anomalies"], "clusters": detection["clusters"], "triaged": triage_result["clusters"]}

    built = dbt_build()
    built >> publish_dbt_metrics()
    detection = detect_anomalies()
    built >> detection
    triaged = triage_anomalies()
    detection >> triaged
    publish_marts(detection, triaged)


ach_transform()
