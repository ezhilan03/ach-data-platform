"""Runtime settings.

Everything is an environment variable with a repo-relative default, so the
same code runs three ways without edits:

* locally (`make pipeline`)            -> defaults under ./data
* inside the Airflow container          -> ACH_LAKE_ROOT=/opt/airflow/data/lake
* in GitHub Actions                     -> small history, no Pushgateway/Grafana

Settings are read at call time (not import time) so tests can override env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# Load .env from the repo root if present. override=False: real env wins.
load_dotenv(REPO_ROOT / ".env", override=False)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value in (None, "") else value


@dataclass(frozen=True)
class Settings:
    # Data layout
    lake_root: Path = field(default_factory=lambda: Path(_env("ACH_LAKE_ROOT", str(REPO_ROOT / "data/lake"))))
    warehouse_path: Path = field(
        default_factory=lambda: Path(_env("ACH_WAREHOUSE_PATH", str(REPO_ROOT / "data/warehouse/ach.duckdb")))
    )
    ground_truth_dir: Path = field(
        default_factory=lambda: Path(_env("ACH_GROUND_TRUTH_DIR", str(REPO_ROOT / "data/ground_truth")))
    )
    runs_dir: Path = field(default_factory=lambda: Path(_env("ACH_RUNS_DIR", str(REPO_ROOT / "data/runs"))))

    # Synthetic history
    base_seed: int = field(default_factory=lambda: int(_env("ACH_SEED", "42")))
    history_days: int = field(default_factory=lambda: int(_env("ACH_HISTORY_DAYS", "60")))
    history_end: date = field(default_factory=lambda: date.fromisoformat(_env("ACH_HISTORY_END", "2026-08-31")))

    # LLM triage
    triage_model: str = field(default_factory=lambda: _env("TRIAGE_MODEL", "ollama_chat/qwen3:4b"))
    ollama_api_base: str = field(default_factory=lambda: _env("OLLAMA_API_BASE", "http://localhost:11434"))

    # Monitoring (empty string = disabled)
    pushgateway_url: str = field(default_factory=lambda: _env("PUSHGATEWAY_URL", ""))
    grafana_url: str = field(default_factory=lambda: _env("GRAFANA_URL", ""))
    grafana_user: str = field(default_factory=lambda: _env("GRAFANA_USER", "admin"))
    grafana_password: str = field(default_factory=lambda: _env("GRAFANA_PASSWORD", "admin"))

    @property
    def history_start(self) -> date:
        from datetime import timedelta

        return self.history_end - timedelta(days=self.history_days - 1)

    def raw_dir(self, table: str) -> Path:
        return self.lake_root / "raw" / table


def get_settings() -> Settings:
    return Settings()
