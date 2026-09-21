#!/usr/bin/env python3
"""Cross-check the dbt marts against an independent pandas implementation.

dbt tests assert that a table satisfies its contracts: keys unique, rates in
[0, 1], no orphan returns. They cannot tell you the aggregation itself is
*right* -- a window frame off by one row, or a join that quietly fans out,
passes every one of them.

So the same numbers are computed a second time, in pandas, from the generator's
own output (tests/reference_marts.py), and compared row by row. Two independent
implementations agreeing is real evidence; one implementation passing its own
tests is not.

Exits non-zero on any mismatch beyond a float tolerance.

Usage:  python scripts/crosscheck_marts.py [--tolerance 1e-9]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

from ach_platform.config import get_settings  # noqa: E402
from ach_platform.generate import Generator  # noqa: E402
from reference_marts import entries_and_returns, originator_daily  # noqa: E402

# Columns to compare. Deliberately not every column: these are the ones the
# detector actually reads, so a silent change in any of them changes what the
# platform claims to find.
COMPARE = [
    "entry_count",
    "file_count",
    "return_count",
    "unauthorized_return_count",
    "admin_return_count",
    "nsf_return_count",
    "duplicate_entry_count",
    "avg_settlement_lag_days",
    "unauthorized_return_rate",
    "admin_return_rate",
    "nsf_return_rate",
    "duplicate_ratio",
    "baseline_entry_count",
    "baseline_entry_sum",
    "baseline_unauthorized_sum",
    "baseline_admin_sum",
    "baseline_nsf_sum",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance", type=float, default=1e-9)
    ap.add_argument("--max-report", type=int, default=10)
    ns = ap.parse_args()

    settings = get_settings()

    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        sql = con.execute("SELECT * FROM marts.fct_originator_daily ORDER BY company_id, effective_entry_date").df()
    finally:
        con.close()

    gen = Generator(settings.base_seed, settings.history_start, settings.history_end)
    entries, returns = entries_and_returns(gen, settings.history_start, settings.history_end)
    ref = originator_daily(entries, returns)

    key = ["company_id", "effective_entry_date"]
    for frame in (sql, ref):
        frame["effective_entry_date"] = pd.to_datetime(frame["effective_entry_date"]).dt.date

    if len(sql) != len(ref):
        print(f"FAIL row count: warehouse={len(sql)} reference={len(ref)}", file=sys.stderr)
        only_sql = set(map(tuple, sql[key].values)) - set(map(tuple, ref[key].values))
        only_ref = set(map(tuple, ref[key].values)) - set(map(tuple, sql[key].values))
        for label, rows in (("warehouse only", only_sql), ("reference only", only_ref)):
            for row in sorted(rows)[: ns.max_report]:
                print(f"  {label}: {row}", file=sys.stderr)
        return 1

    merged = sql.merge(ref, on=key, suffixes=("_sql", "_ref"), how="outer", indicator=True)
    if (merged["_merge"] != "both").any():
        print("FAIL: key sets differ between warehouse and reference", file=sys.stderr)
        return 1

    failures = 0
    width = max(len(c) for c in COMPARE)
    for col in COMPARE:
        a, b = merged[f"{col}_sql"], merged[f"{col}_ref"]
        if col not in sql.columns:
            print(f"FAIL  {col:<{width}}  missing from the warehouse mart", file=sys.stderr)
            failures += 1
            continue
        both_null = a.isna() & b.isna()
        diff = (a.fillna(0).astype(float) - b.fillna(0).astype(float)).abs()
        bad = (~both_null) & (diff > ns.tolerance)
        n_bad = int(bad.sum())
        if n_bad:
            failures += 1
            print(
                f"FAIL  {col:<{width}}  {n_bad} row(s) differ, max delta {diff[bad].max():.6g}",
                file=sys.stderr,
            )
            for _, row in merged.loc[bad, key + [f"{col}_sql", f"{col}_ref"]].head(ns.max_report).iterrows():
                print(
                    f"    {row[key[0]]} {row[key[1]]}: warehouse={row[f'{col}_sql']} reference={row[f'{col}_ref']}",
                    file=sys.stderr,
                )
        else:
            print(f"PASS  {col:<{width}}  {len(merged)} rows agree")

    if failures:
        print(
            f"\n{failures} column(s) disagree between the dbt marts and the reference implementation. "
            f"One of the two is wrong; the SQL is not automatically the right one.",
            file=sys.stderr,
        )
        return 1
    print(f"\nAll {len(COMPARE)} compared columns agree across {len(merged)} originator-days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
