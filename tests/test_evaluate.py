from datetime import date

from ach_platform.evaluate import evaluate_detection, evaluate_triage
from ach_platform.generate import Incident


def _inc(cid, itype, start, end):
    return Incident(f"INC-{cid}", cid, itype, start, end, 1.0, "")


def _cluster(cid, days, signal="admin_rate"):
    days = sorted(days)
    return {
        "cluster_id": f"{cid}:{days[0]}",
        "company_id": cid,
        "start_date": days[0].isoformat(),
        "end_date": days[-1].isoformat(),
        "anomalies": [{"company_id": cid, "day": d.isoformat(), "signal": signal} for d in days],
    }


def test_detection_metrics_basic():
    # Incident Mon 8/3 - Wed 8/5 (3 banking days). Detector flags Tue+Wed and a spurious Friday.
    incidents = [_inc("A", "admin_return_spike", date(2026, 8, 3), date(2026, 8, 5)),
                 _inc("B", "volume_drop", date(2026, 8, 10), date(2026, 8, 11))]
    clusters = [_cluster("A", [date(2026, 8, 4), date(2026, 8, 5)]), _cluster("A", [date(2026, 8, 14)])]
    m = evaluate_detection(incidents, clusters)
    assert (m.day_tp, m.day_fp, m.day_fn) == (2, 1, 1 + 2)
    assert m.incident_detected == 1 and m.incident_total == 2
    assert m.cluster_true == 1 and m.cluster_total == 2
    assert m.mean_detection_delay_days == 1.0
    assert m.by_type_recall == {"admin_return_spike": 1.0, "volume_drop": 0.0}
    assert round(m.day_precision, 3) == 0.667 and round(m.day_recall, 3) == 0.4


def test_weekend_days_are_not_counted_as_truth():
    incidents = [_inc("A", "volume_drop", date(2026, 8, 7), date(2026, 8, 10))]  # Fri..Mon = 2 banking days
    m = evaluate_detection(incidents, [])
    assert m.day_fn == 2


def test_triage_metrics_rules_vs_llm():
    incidents = [_inc("A", "nsf_spike", date(2026, 8, 3), date(2026, 8, 5))]
    records = [
        {"cluster": _cluster("A", [date(2026, 8, 4)]), "rules": {"incident_type": "nsf_spike"}, "llm": {"incident_type": "admin_return_spike"}},
        {"cluster": _cluster("Z", [date(2026, 8, 20)]), "rules": {"incident_type": "volume_drop"}, "llm": {"incident_type": "no_incident"}},
        {"cluster": _cluster("Y", [date(2026, 8, 21)]), "rules": {"incident_type": "volume_drop"}, "llm": None},
    ]
    t = evaluate_triage(incidents, records)
    assert t["rules"]["accuracy_on_true_incidents"] == 1.0
    assert t["llm"]["accuracy_on_true_incidents"] == 0.0
    assert t["llm"]["no_incident_rate_on_fp_clusters"] == 1.0  # 1 of the 1 FP clusters it answered
    assert t["rules"]["no_incident_rate_on_fp_clusters"] == 0.0
    assert t["llm"]["failed"] == 1
    assert t["llm"]["confusion"]["nsf_spike"] == {"admin_return_spike": 1}
