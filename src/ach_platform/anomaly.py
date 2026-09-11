"""Deterministic anomaly detection over fct_originator_daily.

Rules first, model second (the same principle as the reconciliation engine):
everything here is arithmetic on the mart, so it is free, fast, reproducible,
and explainable. The LLM (triage.py) only ever sees what this module flags.

Signals, per originator-day:

    unauthorized_rate   Poisson surprise: P(X >= k | lambda = n * p0) < alpha
    admin_rate          same test, administrative returns
    nsf_rate            same test, NSF returns
    volume_drop         entries < 40% of trailing baseline (with a minimum baseline)
    duplicate_ratio     > 20% of the day's entries are duplicate submissions
    settlement_lag      avg banking-day lag exceeds baseline by >= 1 day

Why a Poisson test instead of a z-score on daily rates: return rates on a
~200-entry day are counts of 0, 1, 2 events. One return is already 0.5%, so a
z-score against a near-zero baseline (or the NACHA 0.5% network threshold,
which is defined on 60-day windows, not days) fires on ordinary noise. The
first eval run measured exactly that: 73 false-positive days from unauthorized
returns alone. Asking "how surprising is k returns given the pooled trailing
rate p0 and today's n entries?" is the right question, and the answer is a
p-value we can threshold honestly.

Rate signals are only evaluated on *matured* days (older than the return
window as of the latest landed date), because a day whose returns have not all
arrived yet always looks healthier than it is.

The pure function `detect()` takes a DataFrame so it can be unit-tested without
DuckDB; `detect_from_warehouse()` is the thin I/O wrapper.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ach_platform.config import Settings

SIGNALS = ("unauthorized_rate", "admin_rate", "nsf_rate", "volume_drop", "duplicate_ratio", "settlement_lag")

RATE_SIGNALS = (
    # signal, observed count column, pooled baseline count column
    ("unauthorized_rate", "unauthorized_return_count", "baseline_unauthorized_sum"),
    ("admin_rate", "admin_return_count", "baseline_admin_sum"),
    ("nsf_rate", "nsf_return_count", "baseline_nsf_sum"),
)


@dataclass(frozen=True)
class DetectorConfig:
    # Rate signals (Poisson surprise)
    alpha: float = 1e-3  # flag when P(X >= k) < alpha; ~1 false positive per 1,000 quiet days
    min_return_count: int = 3  # never flag on fewer events than this, whatever the p-value
    prior_returns: float = 0.5  # pseudo-counts that keep p0 away from zero on quiet baselines
    prior_entries: float = 500.0  # ...i.e. a floor of about 0.1% before any evidence
    min_baseline_days: int = 7
    min_entries_for_rate: int = 30
    return_window_days: int = 14  # calendar days a day needs to "mature" (10 banking days + settlement)
    # Volume
    volume_drop_ratio: float = 0.4
    min_baseline_entries: float = 40.0
    # Duplicates
    duplicate_ratio_threshold: float = 0.2
    # Settlement
    settlement_lag_delta_days: float = 1.0


@dataclass(frozen=True)
class Anomaly:
    company_id: str
    day: date
    signal: str
    observed: float
    baseline: float
    score: float  # -log10(p) for rate signals; ratio for volume; raw value otherwise
    detail: str

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["day"] = self.day.isoformat()
        return payload


@dataclass
class AnomalyCluster:
    """Consecutive flagged days for one originator == one candidate incident."""

    cluster_id: str
    company_id: str
    start_date: date
    end_date: date
    anomalies: list[Anomaly]

    @property
    def signals(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self.anomalies:
            counts[a.signal] = counts.get(a.signal, 0) + 1
        return counts

    @property
    def dominant_signal(self) -> str:
        return max(self.signals.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def to_json(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "company_id": self.company_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "signals": self.signals,
            "dominant_signal": self.dominant_signal,
            "anomalies": [a.to_json() for a in self.anomalies],
        }


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam). Exact summation; k is small here."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    total, term = 0.0, math.exp(-lam)
    for i in range(k):
        total += term
        term *= lam / (i + 1)
    return max(0.0, min(1.0, 1.0 - total))


def _num(v, default: float = 0.0) -> float:
    return default if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def detect(daily: pd.DataFrame, cfg: DetectorConfig = DetectorConfig(), as_of: date | None = None) -> list[Anomaly]:
    """Flag anomalous originator-days. `daily` has fct_originator_daily's columns."""
    if daily.empty:
        return []
    df = daily.copy()
    df["effective_entry_date"] = pd.to_datetime(df["effective_entry_date"]).dt.date
    as_of = as_of or max(df["effective_entry_date"])
    matured_cutoff = as_of - timedelta(days=cfg.return_window_days)

    out: list[Anomaly] = []
    for row in df.itertuples(index=False):
        d: date = row.effective_entry_date
        has_baseline = _num(getattr(row, "baseline_days", 0)) >= cfg.min_baseline_days
        matured = d <= matured_cutoff
        entries = int(_num(row.entry_count))

        # --- rate signals (need maturity + enough entries + a baseline) ------
        if matured and entries >= cfg.min_entries_for_rate and has_baseline:
            base_entries = _num(getattr(row, "baseline_entry_sum", 0))
            for signal, count_col, base_col in RATE_SIGNALS:
                k = int(_num(getattr(row, count_col, 0)))
                base_k = _num(getattr(row, base_col, 0))
                p0 = (base_k + cfg.prior_returns) / (base_entries + cfg.prior_entries)
                lam = entries * p0
                p = poisson_sf(k, lam)
                if k >= cfg.min_return_count and p < cfg.alpha:
                    observed = k / entries
                    score = -math.log10(max(p, 1e-300))
                    why = (f"{k} returns on {entries} entries ({observed:.2%}) vs baseline {p0:.2%} "
                           f"(expected {lam:.1f}; p={p:.1e})")
                    out.append(Anomaly(row.company_id, d, signal, observed, p0, score, why))

        # --- volume drop -----------------------------------------------------
        base_vol = _num(getattr(row, "baseline_entry_count", 0))
        if has_baseline and base_vol >= cfg.min_baseline_entries:
            ratio = entries / base_vol
            if ratio < cfg.volume_drop_ratio:
                out.append(
                    Anomaly(row.company_id, d, "volume_drop", float(entries), base_vol, ratio,
                            f"{entries} entries vs baseline {base_vol:.0f} ({ratio:.0%})")
                )

        # --- duplicate submissions ------------------------------------------
        dup_ratio = _num(getattr(row, "duplicate_ratio", 0))
        if entries >= cfg.min_entries_for_rate and dup_ratio > cfg.duplicate_ratio_threshold:
            out.append(
                Anomaly(row.company_id, d, "duplicate_ratio", dup_ratio, 0.0, dup_ratio,
                        f"{dup_ratio:.0%} of entries are duplicate submissions ({int(_num(row.file_count, 1))} files)")
            )

        # --- settlement lag (banking days) ------------------------------------
        lag = _num(getattr(row, "avg_settlement_lag_days", 0))
        base_lag = _num(getattr(row, "baseline_settlement_lag_days", 0))
        if has_baseline and lag - base_lag >= cfg.settlement_lag_delta_days:
            out.append(
                Anomaly(row.company_id, d, "settlement_lag", lag, base_lag, lag - base_lag,
                        f"avg settlement lag {lag:.1f} banking days vs baseline {base_lag:.1f}")
            )

    return sorted(out, key=lambda a: (a.company_id, a.day, a.signal))


def cluster(anomalies: list[Anomaly], max_gap_days: int = 3) -> list[AnomalyCluster]:
    """Group flagged days per originator into contiguous runs (weekends allowed)."""
    clusters: list[AnomalyCluster] = []
    by_company: dict[str, list[Anomaly]] = {}
    for a in anomalies:
        by_company.setdefault(a.company_id, []).append(a)
    for company_id, items in sorted(by_company.items()):
        items.sort(key=lambda a: a.day)
        current: list[Anomaly] = []
        for a in items:
            if current and (a.day - current[-1].day).days > max_gap_days:
                clusters.append(_make_cluster(company_id, current))
                current = []
            current.append(a)
        if current:
            clusters.append(_make_cluster(company_id, current))
    return clusters


def _make_cluster(company_id: str, items: list[Anomaly]) -> AnomalyCluster:
    start, end = items[0].day, items[-1].day
    return AnomalyCluster(f"{company_id}:{start.isoformat()}", company_id, start, end, list(items))


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #

def load_daily(settings: Settings) -> pd.DataFrame:
    import duckdb

    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        return con.execute("SELECT * FROM marts.fct_originator_daily ORDER BY company_id, effective_entry_date").df()
    finally:
        con.close()


def detect_from_warehouse(settings: Settings, cfg: DetectorConfig = DetectorConfig()) -> tuple[list[Anomaly], list[AnomalyCluster], Path]:
    daily = load_daily(settings)
    anomalies = detect(daily, cfg)
    clusters = cluster(anomalies)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.runs_dir / "anomalies.json"
    out_path.write_text(
        json.dumps(
            {
                "as_of": max(pd.to_datetime(daily["effective_entry_date"]).dt.date).isoformat() if not daily.empty else None,
                "config": asdict(cfg),
                "anomaly_count": len(anomalies),
                "clusters": [c.to_json() for c in clusters],
            },
            indent=2,
        )
    )
    return anomalies, clusters, out_path
