"""Evaluation harness: detector and triage against the labeled incident plan.

Nothing in the platform is "done" until this reports a number. Three layers:

1. Detection, day level     -- precision / recall / F1 over (originator, day)
                               pairs. Truth = every day inside an incident's
                               [start, end]; prediction = every flagged day.
2. Detection, incident level -- recall: incidents with >= 1 flagged day inside
                               them; precision: clusters overlapping an incident.
                               Plus mean detection delay (days from incident
                               start to first flagged day).
3. Triage, cluster level    -- accuracy of incident_type for the rules baseline
                               and the LLM, over clusters that overlap a true
                               incident. On false-positive clusters, the right
                               answer is "no_incident"; that's reported too.

The harness is pure functions over JSON so it runs identically in CI (rules
only, no model) and on the laptop (rules + LLM).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ach_platform.config import Settings
from ach_platform.generate import Incident


@dataclass
class DetectionMetrics:
    day_tp: int
    day_fp: int
    day_fn: int
    incident_total: int
    incident_detected: int
    cluster_total: int
    cluster_true: int
    mean_detection_delay_days: float | None
    by_type_recall: dict[str, float]

    @property
    def day_precision(self) -> float:
        return _safe_div(self.day_tp, self.day_tp + self.day_fp)

    @property
    def day_recall(self) -> float:
        return _safe_div(self.day_tp, self.day_tp + self.day_fn)

    @property
    def day_f1(self) -> float:
        p, r = self.day_precision, self.day_recall
        return _safe_div(2 * p * r, p + r)

    @property
    def incident_recall(self) -> float:
        return _safe_div(self.incident_detected, self.incident_total)

    @property
    def cluster_precision(self) -> float:
        return _safe_div(self.cluster_true, self.cluster_total)

    def to_json(self) -> dict:
        return {
            "day_level": {"tp": self.day_tp, "fp": self.day_fp, "fn": self.day_fn,
                          "precision": round(self.day_precision, 4), "recall": round(self.day_recall, 4), "f1": round(self.day_f1, 4)},
            "incident_level": {"incidents": self.incident_total, "detected": self.incident_detected,
                               "recall": round(self.incident_recall, 4),
                               "clusters": self.cluster_total, "clusters_overlapping_truth": self.cluster_true,
                               "precision": round(self.cluster_precision, 4),
                               "mean_detection_delay_days": self.mean_detection_delay_days},
            "recall_by_incident_type": {k: round(v, 4) for k, v in self.by_type_recall.items()},
        }


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _days(start: date, end: date) -> set[date]:
    return {start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5}


def _overlapping_incident(cluster: dict, incidents: list[Incident]) -> Incident | None:
    cs, ce = date.fromisoformat(cluster["start_date"]), date.fromisoformat(cluster["end_date"])
    for inc in incidents:
        if inc.company_id == cluster["company_id"] and cs <= inc.end_date and ce >= inc.start_date:
            return inc
    return None


def evaluate_detection(incidents: list[Incident], clusters: list[dict]) -> DetectionMetrics:
    truth: set[tuple[str, date]] = set()
    for inc in incidents:
        truth |= {(inc.company_id, d) for d in _days(inc.start_date, inc.end_date)}
    predicted: set[tuple[str, date]] = set()
    for c in clusters:
        predicted |= {(c["company_id"], date.fromisoformat(a["day"])) for a in c["anomalies"]}

    tp = len(truth & predicted)
    fp = len(predicted - truth)
    fn = len(truth - predicted)

    detected, delays = 0, []
    by_type_hit: dict[str, list[int]] = {}
    for inc in incidents:
        hits = sorted(d for (cid, d) in predicted if cid == inc.company_id and inc.start_date <= d <= inc.end_date)
        by_type_hit.setdefault(inc.incident_type, []).append(1 if hits else 0)
        if hits:
            detected += 1
            delays.append((hits[0] - inc.start_date).days)

    cluster_true = sum(1 for c in clusters if _overlapping_incident(c, incidents) is not None)
    return DetectionMetrics(
        day_tp=tp, day_fp=fp, day_fn=fn,
        incident_total=len(incidents), incident_detected=detected,
        cluster_total=len(clusters), cluster_true=cluster_true,
        mean_detection_delay_days=round(sum(delays) / len(delays), 2) if delays else None,
        by_type_recall={t: sum(v) / len(v) for t, v in sorted(by_type_hit.items())},
    )


def evaluate_triage(incidents: list[Incident], records: list[dict]) -> dict:
    """Per-method accuracy over clusters. Returns a JSON-ready dict."""
    methods = ("rules", "llm")
    out: dict = {m: {"n": 0, "correct": 0, "no_incident_on_fp": 0, "fp_clusters": 0, "failed": 0, "confusion": {}} for m in methods}
    for rec in records:
        inc = _overlapping_incident(rec["cluster"], incidents)
        truth_type = inc.incident_type if inc else "no_incident"
        for m in methods:
            pred = rec.get(m)
            stats = out[m]
            if pred is None:
                if m == "llm":
                    stats["failed"] += 1
                continue
            pred_type = pred["incident_type"]
            stats["confusion"].setdefault(truth_type, {}).setdefault(pred_type, 0)
            stats["confusion"][truth_type][pred_type] += 1
            if inc:
                stats["n"] += 1
                stats["correct"] += int(pred_type == truth_type)
            else:
                stats["fp_clusters"] += 1
                stats["no_incident_on_fp"] += int(pred_type == "no_incident")
    for m in methods:
        stats = out[m]
        stats["accuracy_on_true_incidents"] = round(_safe_div(stats["correct"], stats["n"]), 4)
        stats["no_incident_rate_on_fp_clusters"] = round(_safe_div(stats["no_incident_on_fp"], stats["fp_clusters"]), 4)
    return out


def load_incidents(settings: Settings) -> list[Incident]:
    payload = json.loads((settings.ground_truth_dir / "incidents.json").read_text())
    return [Incident.from_json(i) for i in payload["incidents"]]


def run_eval(settings: Settings, out_path: Path | None = None) -> dict:
    incidents = load_incidents(settings)
    anomalies = json.loads((settings.runs_dir / "anomalies.json").read_text())
    triage_path = settings.runs_dir / "triage.json"
    triage = json.loads(triage_path.read_text()) if triage_path.exists() else {"model": None, "records": []}

    detection = evaluate_detection(incidents, anomalies["clusters"])
    result = {
        "as_of": anomalies.get("as_of"),
        "incidents": len(incidents),
        "detector_config": anomalies.get("config"),
        "detection": detection.to_json(),
        "triage": {"model": triage.get("model"), "clusters_evaluated": len(triage["records"]), **evaluate_triage(incidents, triage["records"])},
    }
    out_path = out_path or (settings.runs_dir / "eval.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    return result


def format_report(result: dict) -> str:
    d, i = result["detection"]["day_level"], result["detection"]["incident_level"]
    t = result["triage"]
    lines = [
        f"as_of {result['as_of']}  incidents {result['incidents']}",
        f"detection day-level     P={d['precision']:.3f} R={d['recall']:.3f} F1={d['f1']:.3f}  (tp={d['tp']} fp={d['fp']} fn={d['fn']})",
        f"detection incident-level recall={i['recall']:.3f} ({i['detected']}/{i['incidents']})  "
        f"cluster precision={i['precision']:.3f} ({i['clusters_overlapping_truth']}/{i['clusters']})  "
        f"mean delay={i['mean_detection_delay_days']}d",
        "recall by type          " + ", ".join(f"{k}={v:.2f}" for k, v in result["detection"]["recall_by_incident_type"].items()),
        f"triage rules            acc={t['rules']['accuracy_on_true_incidents']:.3f} (n={t['rules']['n']})",
        f"triage llm [{t['model']}]  acc={t['llm']['accuracy_on_true_incidents']:.3f} (n={t['llm']['n']}, failed={t['llm']['failed']})",
    ]
    return "\n".join(lines)
