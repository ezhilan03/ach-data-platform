"""Land generated rows as Hive-partitioned Parquet in the raw zone of the lake.

Layout (dt = the day the file arrived, i.e. the ingest run's logical date):

    data/lake/raw/ach_entries/dt=2026-08-03/part-0.parquet
    data/lake/raw/ach_files/dt=2026-08-03/part-0.parquet
    data/lake/raw/ach_returns/dt=2026-08-03/part-0.parquet   <- returns *arriving* that day
    data/lake/raw/originators/originators.parquet             <- static dimension

DuckDB is the Parquet writer. Two reasons: (1) it's already the query engine, so
one fewer dependency; (2) explicit CASTs in the COPY statement pin the column
types, which matters because an empty weekend partition still has to carry the
same schema as a busy Monday or `read_parquet(..., union_by_name=true)` would
degrade the column types downstream.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from ach_platform.config import Settings
from ach_platform.generate import Generator

# Column -> DuckDB type. The generator's dict keys must match exactly.
SCHEMAS: dict[str, dict[str, str]] = {
    "ach_entries": {
        "entry_id": "VARCHAR",
        "file_id": "VARCHAR",
        "batch_id": "VARCHAR",
        "trace_number": "VARCHAR",
        "company_id": "VARCHAR",
        "sec_code": "VARCHAR",
        "transaction_code": "VARCHAR",
        "amount_cents": "BIGINT",
        "effective_entry_date": "DATE",
        "settlement_date": "DATE",
        "receiving_dfi_id": "VARCHAR",
        "account_hash": "VARCHAR",
    },
    "ach_files": {
        "file_id": "VARCHAR",
        "company_id": "VARCHAR",
        "file_creation_date": "DATE",
        "file_sequence": "INTEGER",
        "batch_count": "INTEGER",
        "entry_count": "INTEGER",
        "total_debit_cents": "BIGINT",
        "total_credit_cents": "BIGINT",
    },
    "ach_returns": {
        "return_id": "VARCHAR",
        "original_trace_number": "VARCHAR",
        "original_entry_id": "VARCHAR",
        "company_id": "VARCHAR",
        "return_code": "VARCHAR",
        "return_class": "VARCHAR",
        "amount_cents": "BIGINT",
        "original_effective_date": "DATE",
        "return_date": "DATE",
    },
    "originators": {
        "company_id": "VARCHAR",
        "company_name": "VARCHAR",
        "industry": "VARCHAR",
        "sec_code": "VARCHAR",
        "odfi_routing": "VARCHAR",
        "base_daily_volume": "INTEGER",
    },
}


def _to_frame(rows: list[dict], schema: dict[str, str]) -> pd.DataFrame:
    columns = list(schema)
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    return pd.DataFrame(rows, columns=columns)


def write_parquet(rows: list[dict], table: str, path: Path) -> int:
    """Write rows to a single Parquet file with pinned column types. Returns row count.

    Writes to a temp file in the same directory and `os.replace`s it into place.
    Two reasons this matters here: a crashed task can never leave a truncated
    Parquet file that poisons every downstream `read_parquet` glob, and the
    ingest DAG can run several days concurrently (catchup) without two tasks
    interleaving writes to the shared originators dimension.
    """
    schema = SCHEMAS[table]
    frame = _to_frame(rows, schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    casts = ", ".join(f'CAST("{col}" AS {typ}) AS "{col}"' for col, typ in schema.items())
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    con = duckdb.connect()
    try:
        con.register("rows_df", frame)
        con.execute(f"COPY (SELECT {casts} FROM rows_df) TO '{tmp.as_posix()}' (FORMAT PARQUET)")
        con.close()
        os.replace(tmp, path)  # atomic within a filesystem
    finally:
        con.close()
        tmp.unlink(missing_ok=True)
    return len(frame)


def partition_path(settings: Settings, table: str, d: date) -> Path:
    return settings.raw_dir(table) / f"dt={d.isoformat()}" / "part-0.parquet"


def land_day(generator: Generator, d: date, settings: Settings) -> dict:
    """Land one day's entries/files and the returns that arrive that day. Idempotent."""
    bundle = generator.day_bundle(d)
    arriving = generator.returns_arriving_on(d)
    counts = {
        "day": d.isoformat(),
        "ach_entries": write_parquet(bundle.entries, "ach_entries", partition_path(settings, "ach_entries", d)),
        "ach_files": write_parquet(bundle.files, "ach_files", partition_path(settings, "ach_files", d)),
        "ach_returns": write_parquet(arriving, "ach_returns", partition_path(settings, "ach_returns", d)),
    }
    return counts


def land_dimensions(generator: Generator, settings: Settings) -> dict:
    """Static originator dimension + the ground-truth incident plan (for eval only)."""
    n = write_parquet(
        generator.originator_rows(),
        "originators",
        settings.raw_dir("originators") / "originators.parquet",
    )
    settings.ground_truth_dir.mkdir(parents=True, exist_ok=True)
    gt_path = settings.ground_truth_dir / "incidents.json"
    payload = json.dumps(
        {"generator": generator.summary(), "incidents": [i.to_json() for i in generator.incidents]},
        indent=2,
    )
    tmp = gt_path.with_suffix(".json.tmp")
    tmp.write_text(payload)
    os.replace(tmp, gt_path)
    return {"originators": n, "incidents": len(generator.incidents), "ground_truth": str(gt_path)}


def make_generator(settings: Settings) -> Generator:
    return Generator(settings.base_seed, settings.history_start, settings.history_end)
