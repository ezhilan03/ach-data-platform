#!/usr/bin/env python3
"""Import every DAG file and report what was defined.

Run inside the built image, where the real Airflow and Task SDK live. A DAG
file with an import error does not crash anything loudly -- it just quietly
stops being scheduled, and the only signal is a counter in the UI. This makes
that failure a red build instead.

Executing the module runs the `@dag`-decorated factory calls, so this catches
bad decorator arguments and bad asset definitions, not only bad imports.

Usage:  python scripts/parse_dags.py [dags_dir]
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path


def main(dags_dir: str) -> int:
    root = Path(dags_dir)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    # DAG files import each other by bare module name (`from assets import ...`),
    # the same way the DAG processor makes the folder importable.
    sys.path.insert(0, str(root))

    files = sorted(p for p in root.glob("*.py") if not p.name.startswith("_"))
    if not files:
        print(f"no DAG files found in {root}", file=sys.stderr)
        return 2

    failures = 0
    dag_ids: list[str] = []
    for path in files:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            failures += 1
            print(f"FAIL  {path.name}", file=sys.stderr)
            traceback.print_exc()
            continue
        found = []
        for value in vars(module).values():
            dag_id = getattr(value, "dag_id", None)
            if dag_id and type(value).__name__ in ("DAG", "SerializedDAG"):
                found.append(dag_id)
        dag_ids.extend(found)
        print(f"OK    {path.name}" + (f"  -> {', '.join(sorted(found))}" if found else "  (no DAG defined)"))

    print(f"\n{len(files) - failures}/{len(files)} files parsed; DAGs defined: {', '.join(sorted(dag_ids)) or 'none'}")
    if failures:
        print(f"{failures} DAG file(s) failed to import.", file=sys.stderr)
        return 1
    if not dag_ids:
        print("no DAG objects were defined by any file -- the DAG processor would find nothing.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "airflow/dags"))
