"""Pipeline steps as plain functions.

Airflow tasks, the CLI, and CI all call these. Keeping orchestration-agnostic
functions here means the DAG file is thin wiring, and the exact code that runs
in Airflow can be run and tested without Airflow.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

from ach_platform import anomaly as anomaly_mod
from ach_platform import metrics
from ach_platform.config import REPO_ROOT, Settings
from ach_platform.landing import land_day, land_dimensions, make_generator
from ach_platform.validate import validate_day

DBT_PROJECT_DIR = Path(os.environ.get("DBT_PROJECT_DIR", str(REPO_ROOT / "dbt" / "ach_dbt")))


def backfill(settings: Settings, days: int | None = None) -> dict:
    """Land the static dimension plus every day of history. Idempotent."""
    gen = make_generator(settings)
    dims = land_dimensions(gen, settings)
    n_days = days or settings.history_days
    start = settings.history_end - timedelta(days=n_days - 1)
    totals = {"ach_entries": 0, "ach_files": 0, "ach_returns": 0}
    d = start
    while d <= settings.history_end:
        counts = land_day(gen, d, settings)
        for k in totals:
            totals[k] += counts[k]
        d += timedelta(days=1)
    return {"generator": gen.summary(), "dimensions": dims, "days": n_days, "rows": totals}


def ingest_day(settings: Settings, d: date, push: bool = True) -> dict:
    """Land one day, validate it, push metrics. Raises on validation failure."""
    gen = make_generator(settings)
    land_dimensions(gen, settings)  # cheap, keeps the dimension current
    counts = land_day(gen, d, settings)
    report = validate_day(d, settings).to_json()
    if push:
        metrics.push_ingest_metrics(settings, d.isoformat(), counts, report)
    return {"counts": counts, "validation": report}


def dbt_command(settings: Settings, args: list[str] | None = None, dbt_bin: str | None = None) -> list[str]:
    # DBT_BIN points at dbt's own virtualenv inside the Airflow image (ADR-0004).
    dbt_bin = dbt_bin or os.environ.get("DBT_BIN", "dbt")
    return [dbt_bin, *(args or ["build"]), "--project-dir", str(DBT_PROJECT_DIR), "--profiles-dir", str(DBT_PROJECT_DIR)]


def run_dbt(settings: Settings, args: list[str] | None = None, dbt_bin: str | None = None) -> Path:
    env = {**os.environ, "ACH_LAKE_ROOT": str(settings.lake_root), "ACH_WAREHOUSE_PATH": str(settings.warehouse_path)}
    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(dbt_command(settings, args, dbt_bin), check=True, env=env)
    return DBT_PROJECT_DIR / "target" / "run_results.json"


def publish_dbt_metrics(settings: Settings, run_results: Path | None = None) -> dict:
    return metrics.push_dbt_metrics(settings, run_results or DBT_PROJECT_DIR / "target" / "run_results.json")


def detect(settings: Settings, push: bool = True) -> dict:
    anomalies, clusters, out_path = anomaly_mod.detect_from_warehouse(settings)
    by_signal: dict[str, int] = {}
    for a in anomalies:
        by_signal[a.signal] = by_signal.get(a.signal, 0) + 1
    summary = {"anomalies": len(anomalies), "clusters": len(clusters), "by_signal": by_signal, "path": str(out_path)}
    if push:
        import duckdb

        con = duckdb.connect(str(settings.warehouse_path), read_only=True)
        try:
            latest = con.execute(
                """
                SELECT company_id, entry_count, unauthorized_return_rate, admin_return_rate, nsf_return_rate
                FROM marts.fct_originator_daily
                WHERE effective_entry_date = (
                    SELECT max(effective_entry_date) - INTERVAL 14 DAY FROM marts.fct_originator_daily
                )
                """
            ).df().to_dict("records")
            breaches = con.execute(
                """
                SELECT
                    sum(breaches_unauthorized_threshold::int) AS unauthorized,
                    sum(breaches_admin_threshold::int)        AS administrative,
                    sum(breaches_overall_threshold::int)      AS overall
                FROM marts.fct_return_rate_60d
                WHERE as_of_date = (SELECT max(as_of_date) FROM marts.fct_return_rate_60d)
                """
            ).df().to_dict("records")[0]
        finally:
            con.close()
        metrics.push_data_health(settings, latest, by_signal, {k: int(v or 0) for k, v in breaches.items()})
    return summary


def triage(settings: Settings, use_llm: bool = True, annotate: bool = True) -> dict:
    from ach_platform.grafana import annotate_triage
    from ach_platform.triage import triage_clusters

    payload = json.loads((settings.runs_dir / "anomalies.json").read_text())
    clusters = [_cluster_from_json(c) for c in payload["clusters"]]
    out = triage_clusters(clusters, settings, use_llm=use_llm)
    records = json.loads(out.read_text())["records"]
    posted = annotate_triage(settings, records) if annotate else 0
    llm_ok = sum(1 for r in records if r.get("llm"))
    return {"clusters": len(records), "llm_success": llm_ok, "annotations_posted": posted, "path": str(out)}


def _cluster_from_json(c: dict) -> anomaly_mod.AnomalyCluster:
    anomalies = [
        anomaly_mod.Anomaly(a["company_id"], date.fromisoformat(a["day"]), a["signal"], a["observed"], a["baseline"], a["score"], a["detail"])
        for a in c["anomalies"]
    ]
    return anomaly_mod.AnomalyCluster(c["cluster_id"], c["company_id"], date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"]), anomalies)


def evaluate(settings: Settings, out_path: Path | None = None, push: bool = True) -> dict:
    from ach_platform.evaluate import run_eval

    result = run_eval(settings, out_path)
    if push:
        metrics.push_eval_metrics(settings, result)
    return result
