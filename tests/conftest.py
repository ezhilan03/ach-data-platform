"""Shared helpers. Deliberately no DuckDB/dbt here: unit tests run on pandas
alone so they're fast and run anywhere; the warehouse path is exercised by the
CI job that runs the real pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
