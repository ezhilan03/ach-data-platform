"""Detector + eval over real generated data, using the pandas reference marts.

This is the test that says the platform's *claim* holds: the labeled incidents
are recoverable from the numbers. It pins a floor, not the exact figures --
the exact figures are reported by `make eval` and quoted in the README.
"""

from datetime import date

from ach_platform.anomaly import cluster, detect
from ach_platform.evaluate import evaluate_detection
from ach_platform.generate import Generator
from reference_marts import entries_and_returns, originator_daily

START, END = date(2026, 7, 3), date(2026, 8, 31)


def test_detector_recovers_labeled_incidents():
    gen = Generator(42, START, END)
    entries, returns = entries_and_returns(gen, START, END)
    daily = originator_daily(entries, returns)
    anomalies = detect(daily)
    clusters = cluster(anomalies)
    m = evaluate_detection(gen.incidents, [c.to_json() for c in clusters])

    assert m.incident_total == 10
    assert m.incident_recall >= 0.8, m.to_json()
    assert m.cluster_precision >= 0.7, m.to_json()
    assert m.day_f1 >= 0.6, m.to_json()
