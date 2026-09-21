"""Raw-zone validation: is the day's partition fit to enter the warehouse?

These are *contract* checks on the raw files, run before dbt. dbt's own tests
cover the modeled layer; this layer catches the class of problem dbt would
otherwise turn into a confusing compile/runtime error (a missing column, a
NULL key, a return that references an entry we never landed).

Every check returns a count of violations; the report is pushed as metrics and
the task fails on any hard violation so the asset is not published.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import duckdb

from ach_platform.config import Settings
from ach_platform.landing import SCHEMAS, partition_path
from ach_platform.nacha import RETURN_CODES, SEC_CODES, TRANSACTION_CODES


class ValidationError(RuntimeError):
    pass


@dataclass
class ValidationReport:
    day: str
    rows: dict[str, int] = field(default_factory=dict)
    violations: dict[str, int] = field(default_factory=dict)
    warnings: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(v == 0 for v in self.violations.values())

    def to_json(self) -> dict:
        return {
            "day": self.day,
            "ok": self.ok,
            "rows": self.rows,
            "violations": self.violations,
            "warnings": self.warnings,
        }


def _sql_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def validate_day(d: date, settings: Settings) -> ValidationReport:
    report = ValidationReport(day=d.isoformat())
    con = duckdb.connect()
    try:
        for table in ("ach_entries", "ach_files", "ach_returns"):
            path = partition_path(settings, table, d)
            if not path.exists():
                raise ValidationError(f"missing partition: {path}")
            con.execute(f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{path.as_posix()}')")
            cols = {r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
            missing = set(SCHEMAS[table]) - cols
            if missing:
                raise ValidationError(f"{table} missing columns: {sorted(missing)}")
            report.rows[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        def count(name: str, sql: str, hard: bool = True) -> None:
            n = con.execute(sql).fetchone()[0]
            (report.violations if hard else report.warnings)[name] = int(n)

        # --- entries ---------------------------------------------------------
        count(
            "entries_null_keys",
            "SELECT count(*) FROM ach_entries WHERE entry_id IS NULL OR trace_number IS NULL OR company_id IS NULL",
        )
        count("entries_dup_entry_id", "SELECT count(*) - count(DISTINCT entry_id) FROM ach_entries")
        count(
            "entries_dup_trace_in_file",
            "SELECT count(*) - (SELECT count(*) FROM"
            " (SELECT DISTINCT file_id, trace_number FROM ach_entries)) FROM ach_entries",
        )
        count(
            "entries_nonpositive_amount",
            "SELECT count(*) FROM ach_entries WHERE amount_cents IS NULL OR amount_cents <= 0",
        )
        count(
            "entries_bad_sec_code",
            f"SELECT count(*) FROM ach_entries WHERE sec_code NOT IN ({_sql_list(SEC_CODES)})",
        )
        count(
            "entries_bad_tx_code",
            f"SELECT count(*) FROM ach_entries WHERE transaction_code NOT IN ({_sql_list(TRANSACTION_CODES)})",
        )
        count(
            "entries_settles_before_effective",
            "SELECT count(*) FROM ach_entries WHERE settlement_date < effective_entry_date",
        )
        count(
            "entries_wrong_partition",
            f"SELECT count(*) FROM ach_entries WHERE effective_entry_date <> DATE '{d.isoformat()}'",
        )

        # --- files -----------------------------------------------------------
        count(
            "files_entry_count_mismatch",
            """
            SELECT count(*) FROM ach_files f
            LEFT JOIN (SELECT file_id, count(*) AS n FROM ach_entries GROUP BY 1) e USING (file_id)
            WHERE coalesce(e.n, 0) <> f.entry_count
            """,
        )

        # --- returns ---------------------------------------------------------
        count(
            "returns_bad_code",
            f"SELECT count(*) FROM ach_returns WHERE return_code NOT IN ({_sql_list(RETURN_CODES)})",
        )
        count(
            "returns_wrong_partition",
            f"SELECT count(*) FROM ach_returns WHERE return_date <> DATE '{d.isoformat()}'",
        )
        # A return must reference an entry that exists somewhere in the lake.
        # Soft check: on the very first days of a fresh lake the referenced
        # partitions may legitimately not be landed yet.
        entries_glob = (settings.raw_dir("ach_entries") / "*" / "*.parquet").as_posix()
        count(
            "returns_orphan",
            f"""
            SELECT count(*) FROM ach_returns r
            WHERE NOT EXISTS (
                SELECT 1 FROM read_parquet('{entries_glob}', hive_partitioning = true) e
                WHERE e.entry_id = r.original_entry_id
            )
            """,
            hard=False,
        )
    finally:
        con.close()

    if not report.ok:
        failed = {k: v for k, v in report.violations.items() if v}
        raise ValidationError(f"raw validation failed for {d}: {failed}")
    return report
