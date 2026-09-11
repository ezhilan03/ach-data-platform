"""Custom pipeline metrics -> Prometheus Pushgateway.

Airflow's own metrics (task duration, failures, scheduler heartbeat) reach
Prometheus via StatsD. Those tell you whether the *pipeline* is healthy. They
say nothing about whether the *data* is -- rows landed, dbt tests passed,
return rates, anomalies. Those are pushed from the tasks themselves, here.

Pushgateway is the right tool because tasks are short-lived processes that
Prometheus can't scrape. Every push replaces the previous value for the same
(job, grouping_key), so gauges reflect the latest run, not a sum.

If PUSHGATEWAY_URL is unset every function is a no-op: CI and local dry runs
don't need a monitoring stack.
"""

from __future__ import annotations

import json
from pathlib import Path

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from ach_platform.config import Settings

JOB = "ach_pipeline"


def _push(settings: Settings, registry: CollectorRegistry, grouping_key: dict[str, str]) -> bool:
    if not settings.pushgateway_url:
        return False
    push_to_gateway(settings.pushgateway_url, job=JOB, registry=registry, grouping_key=grouping_key)
    return True


def push_ingest_metrics(settings: Settings, day: str, counts: dict[str, int], validation: dict) -> bool:
    reg = CollectorRegistry()
    rows = Gauge("ach_ingest_rows_landed", "Rows landed in the raw zone for the run day", ["table"], registry=reg)
    for table in ("ach_entries", "ach_files", "ach_returns"):
        rows.labels(table=table).set(counts.get(table, 0))
    viol = Gauge("ach_raw_validation_violations", "Hard validation violations by check", ["check"], registry=reg)
    for check, n in (validation.get("violations") or {}).items():
        viol.labels(check=check).set(n)
    warn = Gauge("ach_raw_validation_warnings", "Soft validation findings by check", ["check"], registry=reg)
    for check, n in (validation.get("warnings") or {}).items():
        warn.labels(check=check).set(n)
    Gauge("ach_ingest_last_run_day", "Run day as YYYYMMDD integer", registry=reg).set(int(day.replace("-", "")))
    return _push(settings, reg, {"stage": "ingest"})


def push_dbt_metrics(settings: Settings, run_results_path: Path) -> dict:
    """Parse dbt's target/run_results.json and push model/test outcomes."""
    payload = json.loads(run_results_path.read_text())
    results = payload.get("results", [])
    summary = {"models": {}, "tests": {}, "elapsed": payload.get("elapsed_time")}
    reg = CollectorRegistry()
    by_status = Gauge("ach_dbt_nodes", "dbt nodes by resource type and status", ["resource_type", "status"], registry=reg)
    model_secs = Gauge("ach_dbt_model_execution_seconds", "Execution time per dbt model", ["model"], registry=reg)
    counts: dict[tuple[str, str], int] = {}
    for r in results:
        unique_id = r.get("unique_id", "")
        rtype = unique_id.split(".")[0] if unique_id else "unknown"
        status = r.get("status", "unknown")
        counts[(rtype, status)] = counts.get((rtype, status), 0) + 1
        if rtype == "model":
            model_secs.labels(model=unique_id.split(".")[-1]).set(float(r.get("execution_time") or 0.0))
    for (rtype, status), n in counts.items():
        by_status.labels(resource_type=rtype, status=status).set(n)
        summary.setdefault(rtype + "s", {})[status] = n
    Gauge("ach_dbt_run_elapsed_seconds", "Wall time of the dbt invocation", registry=reg).set(float(payload.get("elapsed_time") or 0.0))
    _push(settings, reg, {"stage": "dbt"})
    return summary


def push_data_health(settings: Settings, daily_latest: list[dict], anomalies_by_signal: dict[str, int], breaches: dict[str, int]) -> bool:
    """daily_latest: one row per originator for the latest matured day."""
    reg = CollectorRegistry()
    unauth = Gauge("ach_originator_unauthorized_return_rate", "Unauthorized return rate, latest matured day", ["company_id"], registry=reg)
    admin = Gauge("ach_originator_admin_return_rate", "Administrative return rate, latest matured day", ["company_id"], registry=reg)
    nsf = Gauge("ach_originator_nsf_return_rate", "NSF return rate, latest matured day", ["company_id"], registry=reg)
    vol = Gauge("ach_originator_entry_count", "Entries originated, latest day", ["company_id"], registry=reg)
    for row in daily_latest:
        cid = row["company_id"]
        unauth.labels(company_id=cid).set(row.get("unauthorized_return_rate") or 0.0)
        admin.labels(company_id=cid).set(row.get("admin_return_rate") or 0.0)
        nsf.labels(company_id=cid).set(row.get("nsf_return_rate") or 0.0)
        vol.labels(company_id=cid).set(row.get("entry_count") or 0)
    anom = Gauge("ach_anomalies_flagged", "Anomalous originator-days flagged in the latest run", ["signal"], registry=reg)
    for signal, n in anomalies_by_signal.items():
        anom.labels(signal=signal).set(n)
    br = Gauge("ach_nacha_60d_breaches", "Originators over a NACHA 60-day threshold as of the latest day", ["threshold"], registry=reg)
    for threshold, n in breaches.items():
        br.labels(threshold=threshold).set(n)
    return _push(settings, reg, {"stage": "data_health"})


def push_eval_metrics(settings: Settings, result: dict) -> bool:
    """Publish the evaluation harness's own numbers.

    Detector quality is an operational metric, not a one-off README figure: if
    a change to the marts or the thresholds quietly degrades recall, the
    dashboard should show it on the next run, not the next time someone reads
    the repo.
    """
    reg = CollectorRegistry()
    day = result["detection"]["day_level"]
    inc = result["detection"]["incident_level"]
    det = Gauge("ach_eval_detection", "Detector quality against labeled incidents", ["metric", "level"], registry=reg)
    for name in ("precision", "recall", "f1"):
        det.labels(metric=name, level="day").set(day[name])
    det.labels(metric="recall", level="incident").set(inc["recall"])
    det.labels(metric="precision", level="cluster").set(inc["precision"])
    if inc.get("mean_detection_delay_days") is not None:
        Gauge("ach_eval_detection_delay_days", "Mean days from incident start to first flag", registry=reg).set(
            inc["mean_detection_delay_days"]
        )
    tri = Gauge("ach_eval_triage_accuracy", "Incident-type accuracy on clusters overlapping a true incident", ["method"], registry=reg)
    for method in ("rules", "llm"):
        tri.labels(method=method).set(result["triage"][method]["accuracy_on_true_incidents"])
    Gauge("ach_eval_triage_failures", "Clusters where the model produced no schema-valid result", registry=reg).set(
        result["triage"]["llm"]["failed"]
    )
    return _push(settings, reg, {"stage": "eval"})
