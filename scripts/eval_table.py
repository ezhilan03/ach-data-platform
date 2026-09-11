#!/usr/bin/env python3
"""Print the README results table from data/runs/eval.json.

The numbers in the README are generated, not typed. A hand-maintained results
table drifts from reality the first time someone tunes a threshold and forgets,
and a portfolio repo whose headline metrics no longer match its own harness is
worse than one with no metrics at all.

Usage:  python scripts/eval_table.py [--eval data/runs/eval.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", default=str(REPO_ROOT / "data/runs/eval.json"))
    ns = ap.parse_args()
    r = json.loads(Path(ns.eval).read_text())
    day, inc, tri = r["detection"]["day_level"], r["detection"]["incident_level"], r["triage"]

    cfg = r.get("detector_config") or {}
    print(f"{r['incidents']} labeled incidents, history through {r['as_of']}, "
          f"alpha={cfg.get('alpha')}, return window {cfg.get('return_window_days')} days.\n")
    print("| Measure | Value | What it means |")
    print("| --- | --- | --- |")
    rows = [
        ("Incident recall", f"{inc['recall']:.2f} ({inc['detected']}/{inc['incidents']})",
         "Labeled incidents with at least one flagged day"),
        ("Cluster precision", f"{inc['precision']:.2f} ({inc['clusters_overlapping_truth']}/{inc['clusters']})",
         "Flagged clusters that overlap a real incident"),
        ("Day-level precision", f"{day['precision']:.2f}", "Flagged originator-days that are inside an incident"),
        ("Day-level recall", f"{day['recall']:.2f}", "Incident days the detector flagged"),
        ("Day-level F1", f"{day['f1']:.2f}", f"tp={day['tp']}, fp={day['fp']}, fn={day['fn']}"),
        ("Mean detection delay", f"{inc['mean_detection_delay_days']} days", "Incident start to first flag"),
        ("Triage accuracy — rules", f"{tri['rules']['accuracy_on_true_incidents']:.2f} (n={tri['rules']['n']})",
         "Baseline, given the detector signal"),
    ]
    if tri["llm"]["n"]:
        rows.append((f"Triage accuracy — LLM (`{tri['model']}`)",
                     f"{tri['llm']['accuracy_on_true_incidents']:.2f} (n={tri['llm']['n']})",
                     "Model, from the evidence packet alone"))
        rows.append(("LLM calls with no schema-valid result", str(tri["llm"]["failed"]),
                     "After one retry with the validation error"))
    else:
        rows.append(("Triage accuracy — LLM", "not measured in this run", "No model configured"))
    for name, value, meaning in rows:
        print(f"| {name} | **{value}** | {meaning} |")

    print("\nRecall by incident type: " + ", ".join(
        f"`{k}` {v:.2f}" for k, v in r["detection"]["recall_by_incident_type"].items()))


if __name__ == "__main__":
    main()
