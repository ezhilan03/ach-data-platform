"""ach_eval -- score the detector and the triage step against the labeled incidents.

This DAG exists because "the pipeline ran green" and "the pipeline found the
problems it was built to find" are different claims with different evidence.
Airflow's own metrics cover the first. This covers the second, on every refresh
of the marts, and pushes the numbers to the same Prometheus the pipeline
metrics land in -- so a change that quietly costs 20 points of recall shows up
as a falling line on a dashboard rather than as a surprise months later.

Scheduled on ACH_MARTS: it can only run on marts that passed their dbt tests.

The ground truth (data/ground_truth/incidents.json) is written by the
generator and read by nothing else in the pipeline. The detector and the LLM
never see it.
"""

from __future__ import annotations

import pendulum
from airflow.sdk import dag, task
from assets import ACH_MARTS

from ach_platform.config import get_settings
from ach_platform.evaluate import format_report
from ach_platform.pipeline import evaluate as evaluate_impl

SETTINGS = get_settings()


@dag(
    dag_id="ach_eval",
    description="Measure detector precision/recall and triage accuracy against labeled incidents",
    schedule=[ACH_MARTS],
    start_date=pendulum.datetime(2026, 7, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["ach", "eval", "ai"],
    doc_md=__doc__,
)
def ach_eval():
    @task
    def score() -> dict:
        result = evaluate_impl(SETTINGS)
        print(format_report(result))
        return result

    score()


ach_eval()
