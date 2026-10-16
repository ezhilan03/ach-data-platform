"""The monitoring stack is a contract, and nothing else checks it.

A Grafana panel that queries a metric name nothing emits does not fail. It
renders "No data" — indistinguishable at a glance from a healthy system with
nothing to report. The same is true of an alert rule: a typo in the expression
produces a rule that can never fire, which is worse than no rule because it
looks like coverage.

These tests close that gap statically, so a renamed metric breaks the build
instead of quietly blanking a panel. They need no Docker, no Prometheus and no
Grafana, which is the point: the stack they describe is the part of this
project that is hardest to exercise in CI.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DASHBOARDS = sorted((REPO / "monitoring/grafana/dashboards").glob("*.json"))
ALERTS = REPO / "monitoring/prometheus/alerts.yml"
PROMETHEUS = REPO / "monitoring/prometheus/prometheus.yml"
STATSD_MAPPING = REPO / "monitoring/statsd/mapping.yml"
COMPOSE = REPO / "docker-compose.yml"

# Airflow metrics that reach Prometheus by passthrough rather than an explicit
# mapping rule: statsd_exporter lowercases and replaces dots with underscores,
# and DogStatsD tags become labels. Names per the Airflow 3 metrics reference.
AIRFLOW_PASSTHROUGH = {
    "airflow_scheduler_heartbeat",
    "airflow_dagbag_size",
    "airflow_dag_processing_import_errors",
    "airflow_ti_successes",
    "airflow_ti_failures",
    "airflow_executor_running_tasks",
    "airflow_executor_queued_tasks",
    "airflow_pool_open_slots",
    "airflow_pool_running_slots",
    "airflow_task_instance_created",
    "airflow_ti_start",
}


def _emitted_metrics() -> dict[str, set[str]]:
    """{metric_name: {label, ...}} for every Gauge constructed in metrics.py."""
    tree = ast.parse((REPO / "src/ach_platform/metrics.py").read_text())
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Gauge"):
            continue
        args = [a for a in node.args if isinstance(a, ast.Constant)]
        if not args:
            continue
        name = args[0].value
        labels: set[str] = set()
        for arg in node.args[2:]:
            if isinstance(arg, ast.List):
                labels = {e.value for e in arg.elts if isinstance(e, ast.Constant)}
        out[name] = labels
    return out


def _mapped_airflow_metrics() -> set[str]:
    """Names produced by explicit statsd_exporter mapping rules, including the
    series a histogram actually exposes."""
    mapping = yaml.safe_load(STATSD_MAPPING.read_text())
    names: set[str] = set()
    for rule in mapping.get("mappings", []):
        name = rule["name"]
        names.add(name)
        if rule.get("observer_type") == "histogram":
            names |= {f"{name}_bucket", f"{name}_sum", f"{name}_count"}
    return names


def _promql_expressions() -> list[tuple[str, str]]:
    """(source, expr) for every dashboard panel target and alert rule."""
    found: list[tuple[str, str]] = []
    for f in DASHBOARDS:
        for panel in json.loads(f.read_text())["panels"]:
            for target in panel.get("targets", []):
                found.append((f"{f.name}:{panel['title']}", target["expr"]))
    for group in yaml.safe_load(ALERTS.read_text())["groups"]:
        for rule in group["rules"]:
            found.append((f"alerts.yml:{rule['alert']}", rule["expr"]))
    return found


METRIC_RE = re.compile(r"\b(ach_[a-z0-9_]+|airflow_[a-z0-9_]+)\b")
SELECTOR_RE = re.compile(r"\b(ach_[a-z0-9_]+)\s*\{([^}]*)\}")
LABEL_RE = re.compile(r"(\w+)\s*(?:=~|!~|!=|=)")


def test_every_queried_metric_is_actually_emitted():
    emitted = _emitted_metrics()
    known = set(emitted) | _mapped_airflow_metrics() | AIRFLOW_PASSTHROUGH
    unknown: list[str] = []
    for source, expr in _promql_expressions():
        for metric in METRIC_RE.findall(expr):
            if metric not in known:
                unknown.append(f"{source}: {metric}")
    assert not unknown, "queried but never emitted:\n  " + "\n  ".join(sorted(set(unknown)))


def test_every_emitted_metric_is_used_somewhere():
    """The reverse direction: a metric nobody charts or alerts on is dead weight
    being pushed on every run."""
    emitted = set(_emitted_metrics())
    used = {m for _, expr in _promql_expressions() for m in METRIC_RE.findall(expr)}
    unused = emitted - used
    assert not unused, f"emitted but never queried: {sorted(unused)}"


def test_label_matchers_reference_declared_labels():
    emitted = _emitted_metrics()
    bad: list[str] = []
    for source, expr in _promql_expressions():
        for metric, selector in SELECTOR_RE.findall(expr):
            if metric not in emitted:
                continue
            for label in LABEL_RE.findall(selector):
                if label not in emitted[metric]:
                    bad.append(f"{source}: {metric}{{{label}=...}} — declared: {sorted(emitted[metric])}")
    assert not bad, "label matchers on undeclared labels:\n  " + "\n  ".join(sorted(set(bad)))


def test_grafana_legends_reference_declared_labels():
    """A legendFormat of {{company_id}} on a metric with no company_id label
    renders the literal template string on every series."""
    emitted = _emitted_metrics()
    bad: list[str] = []
    for f in DASHBOARDS:
        for panel in json.loads(f.read_text())["panels"]:
            for target in panel.get("targets", []):
                legend = target.get("legendFormat")
                if not legend:
                    continue
                metrics = [m for m in METRIC_RE.findall(target["expr"]) if m in emitted]
                if not metrics:
                    continue
                allowed = set().union(*(emitted[m] for m in metrics))
                for ref in re.findall(r"\{\{\s*(\w+)\s*\}\}", legend):
                    if ref not in allowed:
                        bad.append(f"{f.name}:{panel['title']}: {{{{{ref}}}}} not in {sorted(allowed)}")
    assert not bad, "legend references undeclared labels:\n  " + "\n  ".join(sorted(set(bad)))


def test_compose_mounts_point_at_files_that_exist():
    """The classic compose failure: bind-mounting a config path that doesn't
    exist, which Docker silently creates as an empty directory."""
    compose = yaml.safe_load(COMPOSE.read_text())
    missing: list[str] = []
    for name, svc in compose["services"].items():
        for vol in svc.get("volumes", []) or []:
            if not isinstance(vol, str) or not vol.startswith("./"):
                continue
            host = vol.split(":")[0]
            if not (REPO / host[2:]).exists():
                missing.append(f"{name}: {host}")
    assert not missing, "compose mounts a path that does not exist:\n  " + "\n  ".join(missing)


def test_prometheus_scrapes_services_that_exist_on_the_right_ports():
    compose = yaml.safe_load(COMPOSE.read_text())
    prom = yaml.safe_load(PROMETHEUS.read_text())
    problems: list[str] = []
    for job in prom["scrape_configs"]:
        for static in job["static_configs"]:
            for target in static["targets"]:
                host, _, port = target.partition(":")
                if host in ("localhost", "127.0.0.1"):
                    continue
                if host not in compose["services"]:
                    problems.append(f"{job['job_name']}: no compose service named {host!r}")
                    continue
                published = " ".join(str(p) for p in compose["services"][host].get("ports", []) or [])
                command = " ".join(compose["services"][host].get("command", []) or [])
                if port not in published and port not in command:
                    problems.append(f"{job['job_name']}: {host} does not expose :{port}")
    assert not problems, "prometheus scrape config:\n  " + "\n  ".join(problems)


def test_prometheus_rule_files_are_mounted_where_prometheus_expects_them():
    prom = yaml.safe_load(PROMETHEUS.read_text())
    compose = yaml.safe_load(COMPOSE.read_text())
    targets = {v.split(":")[1] for v in compose["services"]["prometheus"]["volumes"] if v.startswith("./")}
    for rule_file in prom.get("rule_files", []):
        assert rule_file in targets, f"{rule_file} is in rule_files but not mounted into the prometheus service"


def test_grafana_provisioning_path_matches_its_mount():
    compose = yaml.safe_load(COMPOSE.read_text())
    targets = {v.split(":")[1] for v in compose["services"]["grafana"]["volumes"] if v.startswith("./")}
    provider = yaml.safe_load((REPO / "monitoring/grafana/provisioning/dashboards/dashboards.yml").read_text())
    for p in provider["providers"]:
        assert p["options"]["path"] in targets, (
            f"dashboard provider reads {p['options']['path']}, which the compose file does not mount"
        )


def test_dashboards_use_the_provisioned_datasource_uid():
    uid = yaml.safe_load((REPO / "monitoring/grafana/provisioning/datasources/prometheus.yml").read_text())[
        "datasources"
    ][0]["uid"]
    for f in DASHBOARDS:
        for panel in json.loads(f.read_text())["panels"]:
            if panel["type"] == "row":
                continue
            assert panel["datasource"]["uid"] == uid, f"{f.name}:{panel['title']} points at a different datasource"


def test_statsd_mapping_matches_the_exporter_schema():
    """statsd_exporter validates its config at startup and exits if it is wrong.
    The container then crash-loops, Prometheus has no Airflow target, and every
    pipeline-health panel reads "No data" -- which is indistinguishable from a
    healthy system with nothing to report. That is exactly what shipped: the
    first stack bring-up had `quantiles` at the wrong level and a `timer_type`
    key the exporter does not accept.
    """
    mapping = yaml.safe_load(STATSD_MAPPING.read_text())

    defaults = mapping.get("defaults", {})
    assert "quantiles" not in defaults, "quantiles belongs under summary_options, not defaults"
    assert "timer_type" not in defaults, "timer_type is not accepted; use observer_type"
    allowed_defaults = {
        "observer_type",
        "match_type",
        "glob_disable_ordering",
        "ttl",
        "summary_options",
        "histogram_options",
    }
    unknown = set(defaults) - allowed_defaults
    assert not unknown, f"unknown keys under defaults: {sorted(unknown)}"
    if defaults.get("observer_type") == "summary":
        assert "quantiles" in defaults.get("summary_options", {}), "summary defaults need summary_options.quantiles"

    for rule in mapping.get("mappings", []):
        assert "match" in rule and "name" in rule, f"rule missing match/name: {rule}"
        assert "quantiles" not in rule, f"{rule['name']}: quantiles belongs under summary_options"
        assert "timer_type" not in rule, f"{rule['name']}: timer_type is not accepted"
        if rule.get("observer_type") == "histogram":
            assert "buckets" in rule.get("histogram_options", {}), (
                f"{rule['name']}: histogram needs histogram_options.buckets"
            )
        assert "buckets" not in rule, f"{rule['name']}: buckets belongs under histogram_options"
