"""`python -m ach_platform <command>` -- the same steps Airflow runs, from a shell.

backfill              land dimensions + full history into the raw zone
ingest --date D       land + validate one day (what the daily DAG does)
dbt [args...]         run dbt with the project/profile dirs wired (default: build)
dbt-metrics           push target/run_results.json to the Pushgateway
detect                run the anomaly detector over the marts
triage [--no-llm]     triage detected clusters (rules baseline always; LLM optional)
eval                  score detector + triage against the labeled incidents
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from ach_platform import pipeline
from ach_platform.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ach_platform",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("backfill")
    p.add_argument("--days", type=int, default=None)

    p = sub.add_parser("ingest")
    p.add_argument("--date", required=True)
    p.add_argument("--no-push", action="store_true")

    p = sub.add_parser("dbt")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.add_argument("--dbt-bin", default=None, help="defaults to $DBT_BIN, else 'dbt'")

    sub.add_parser("dbt-metrics")

    p = sub.add_parser("detect")
    p.add_argument("--no-push", action="store_true")

    p = sub.add_parser("triage")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-annotate", action="store_true")

    p = sub.add_parser("eval")
    p.add_argument("--out", default=None)

    ns = parser.parse_args(argv)
    settings = get_settings()

    if ns.command == "backfill":
        result = pipeline.backfill(settings, ns.days)
    elif ns.command == "ingest":
        result = pipeline.ingest_day(settings, date.fromisoformat(ns.date), push=not ns.no_push)
    elif ns.command == "dbt":
        args = [a for a in ns.args if a != "--"] or ["build"]
        result = {"run_results": str(pipeline.run_dbt(settings, args, ns.dbt_bin))}
    elif ns.command == "dbt-metrics":
        result = pipeline.publish_dbt_metrics(settings)
    elif ns.command == "detect":
        result = pipeline.detect(settings, push=not ns.no_push)
    elif ns.command == "triage":
        result = pipeline.triage(settings, use_llm=not ns.no_llm, annotate=not ns.no_annotate)
    elif ns.command == "eval":
        from ach_platform.evaluate import format_report

        result = pipeline.evaluate(settings, Path(ns.out) if ns.out else None)
        print(format_report(result), file=sys.stderr)
    else:  # pragma: no cover
        parser.error(f"unknown command {ns.command}")
        return 2

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
