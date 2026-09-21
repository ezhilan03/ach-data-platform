#!/usr/bin/env python3
"""Gate a build on the measured quality of the detector, not just on it running.

`dbt build` passing says the models compile and the data satisfies its
contracts. It says nothing about whether the detector still finds the
incidents. This script closes that gap: it reads the evaluation harness's
output and fails the build when a number drops below the committed floor in
eval/thresholds.json.

Usage:  python scripts/check_eval.py [--eval data/runs/eval.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", default=str(REPO_ROOT / "data/runs/eval.json"))
    ap.add_argument("--thresholds", default=str(REPO_ROOT / "eval/thresholds.json"))
    ns = ap.parse_args()

    result = json.loads(Path(ns.eval).read_text())
    floors = json.loads(Path(ns.thresholds).read_text())

    day = result["detection"]["day_level"]
    inc = result["detection"]["incident_level"]
    tri = result["triage"]

    checks: list[tuple[str, float, float, bool]] = [
        (
            "detection.day_f1",
            day["f1"],
            floors["detection"]["day_f1"],
            day["f1"] >= floors["detection"]["day_f1"],
        ),
        (
            "detection.day_precision",
            day["precision"],
            floors["detection"]["day_precision"],
            day["precision"] >= floors["detection"]["day_precision"],
        ),
        (
            "detection.incident_recall",
            inc["recall"],
            floors["detection"]["incident_recall"],
            inc["recall"] >= floors["detection"]["incident_recall"],
        ),
        (
            "detection.cluster_precision",
            inc["precision"],
            floors["detection"]["cluster_precision"],
            inc["precision"] >= floors["detection"]["cluster_precision"],
        ),
        (
            "triage.rules_accuracy",
            tri["rules"]["accuracy_on_true_incidents"],
            floors["triage"]["rules_accuracy"],
            tri["rules"]["accuracy_on_true_incidents"] >= floors["triage"]["rules_accuracy"],
        ),
    ]
    delay = inc.get("mean_detection_delay_days")
    max_delay = floors["detection"]["max_mean_detection_delay_days"]
    if delay is not None:
        checks.append(("detection.mean_detection_delay_days (max)", delay, max_delay, delay <= max_delay))

    width = max(len(name) for name, *_ in checks)
    failures = 0
    for name, value, floor, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  measured={value:<8.4f} floor={floor}")
        failures += not ok

    # Not a gate, but the number a reviewer wants next to the others.
    llm = tri["llm"]
    if llm["n"]:
        print(
            f"info  triage.llm_accuracy{'':<{max(width - 19, 0)}}  measured={llm['accuracy_on_true_incidents']:.4f} "
            f"(n={llm['n']}, no schema-valid result on {llm['failed']}) model={tri['model']}"
        )
    else:
        print("info  triage.llm_accuracy: not measured in this run (no model configured)")

    if failures:
        print(
            f"\n{failures} quality floor(s) breached. Either the change regressed detection, "
            f"or the floor in eval/thresholds.json needs a deliberate, reviewed update.",
            file=sys.stderr,
        )
        return 1
    print("\nAll quality floors met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
