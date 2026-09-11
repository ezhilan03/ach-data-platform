"""ach_ingest -- land one banking day of ACH data and validate it.

Schedule: @daily over the synthetic history window, with catchup ON. Unpausing
the DAG replays the whole history one run per day, which is what a real
historical load looks like and what makes the pipeline-health dashboard worth
looking at (dozens of runs, real durations, real retries).

The unit of work is one day, and `land_day` is a pure function of (seed, day),
so a retry or a cleared run rewrites byte-identical Parquet. That property is
the whole reason the DAG can be this simple: no bookkeeping table, no
"has this day already been processed" check, no partial-write cleanup.

Weekends have no ACH activity, so those runs skip and, because the asset is
attached to the landing task, the downstream chain correctly does not fire.
"""

from __future__ import annotations

import pendulum
from airflow.exceptions import AirflowSkipException
from airflow.sdk import dag, task
from assets import ACH_RAW_DAILY

from ach_platform.config import get_settings
from ach_platform.generate import is_banking_day
from ach_platform.pipeline import ingest_day

SETTINGS = get_settings()


def _utc(d) -> pendulum.DateTime:
    return pendulum.datetime(d.year, d.month, d.day, tz="UTC")


@dag(
    dag_id="ach_ingest",
    description="Land and validate one banking day of raw ACH entries, files and returns",
    schedule="@daily",
    start_date=_utc(SETTINGS.history_start),
    end_date=_utc(SETTINGS.history_end),
    catchup=True,
    max_active_runs=4,  # bounded so a 60-day catchup doesn't swamp a laptop
    tags=["ach", "ingest", "raw"],
    default_args={"retries": 2, "retry_delay": pendulum.duration(seconds=30)},
    doc_md=__doc__,
)
def ach_ingest():
    @task(outlets=[ACH_RAW_DAILY])
    def land_and_validate(**context) -> dict:
        """Generate -> land Parquet -> validate the raw contract -> push metrics.

        Validation runs in the same task as landing on purpose. Splitting them
        would publish the asset event on a partition that had not been checked
        yet, and every downstream consumer would be racing the check.
        """
        day = context["data_interval_start"].date()
        if not is_banking_day(day):
            raise AirflowSkipException(f"{day} is not a banking day -- no ACH activity to land")
        result = ingest_day(SETTINGS, day)
        counts = result["counts"]
        print(
            f"landed {day}: {counts['ach_entries']} entries, {counts['ach_files']} files, "
            f"{counts['ach_returns']} returns arriving; validation warnings={result['validation']['warnings']}"
        )
        return result

    land_and_validate()


ach_ingest()
