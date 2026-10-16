#!/usr/bin/env python3
"""Parse the DAG folder exactly the way Airflow's DAG processor does.

Run inside the built image, where the real Airflow lives. A DAG file with an
import error does not crash anything loudly -- it stops being scheduled, and
the only signal is a counter in the UI. This makes that a red build instead.

This uses Airflow's own DagBag rather than importing the files by hand, and
that distinction is the whole point. An earlier version of this script did
`sys.path.insert(0, dags_dir)` before importing each file, which made a
sibling `from assets import ...` resolve here and nowhere else. Airflow 3 does
NOT put the DAGs folder on sys.path, so all three DAGs failed to import in the
real processor while this script reported a clean parse. A verification tool
that is more permissive than production is worse than no tool at all.

Usage:  python scripts/parse_dags.py [dags_dir]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main(dags_dir: str) -> int:
    root = Path(dags_dir).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    # Configure the folder before importing airflow so DagBag and the processor
    # agree on what they are parsing.
    os.environ.setdefault("AIRFLOW__CORE__DAGS_FOLDER", str(root))
    os.environ.setdefault("AIRFLOW__CORE__LOAD_EXAMPLES", "False")

    try:
        from airflow.dag_processing.dagbag import DagBag
    except ImportError:  # Airflow < 3.3
        from airflow.models.dagbag import DagBag

    bag = DagBag(str(root))

    files = sorted(p.name for p in root.glob("*.py") if not p.name.startswith("_"))
    if not files:
        print(f"no DAG files found in {root}", file=sys.stderr)
        return 2

    for dag_id in sorted(bag.dags):
        dag = bag.dags[dag_id]
        schedule = getattr(dag, "schedule", None) or getattr(dag, "timetable", None)
        print(f"OK    {dag_id:<16} schedule={schedule!s:<40.40} tasks={len(dag.tasks)}")

    for path, err in bag.import_errors.items():
        print(f"FAIL  {Path(path).name}", file=sys.stderr)
        print("      " + err.strip().splitlines()[-1], file=sys.stderr)

    print(f"\n{len(files)} file(s) scanned, {len(bag.dags)} DAG(s) found, {len(bag.import_errors)} import error(s)")

    if bag.import_errors:
        return 1
    if not bag.dags:
        print("no DAGs were found -- the processor would schedule nothing.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "airflow/dags"))
