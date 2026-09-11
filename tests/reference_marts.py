"""A pandas re-implementation of int_originator_daily + fct_originator_daily.

Used by tests to check the detector end-to-end on generated data without a
warehouse, and -- once the real pipeline has run -- to cross-check the dbt
marts against an independent implementation (`make crosscheck`). If the two
disagree, one of them has a bug; the SQL is not automatically right.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ach_platform.generate import Generator


def banking_days_between(a: date, b: date) -> int:
    """Mon-Fri days in (a, b], counted one by one (the SQL macro is a closed form)."""
    return sum(1 for k in range(1, (b - a).days + 1) if (a + pd.Timedelta(days=k)).weekday() < 5)


def entries_and_returns(gen: Generator, start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    entries, returns = [], []
    d = start
    while d <= end:
        b = gen.day_bundle(d)
        entries.extend(b.entries)
        # Only returns that have *arrived* by `end` exist in the lake.
        returns.extend(r for r in b.returns if r["return_date"] <= end)
        d = d.fromordinal(d.toordinal() + 1)
    return pd.DataFrame(entries), pd.DataFrame(returns)


def originator_daily(entries: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    e = entries.copy()
    e["is_debit"] = e.transaction_code.isin(["27", "37"])
    e["settlement_lag_days"] = [
        banking_days_between(a, b) for a, b in zip(e.effective_entry_date, e.settlement_date, strict=True)
    ]
    dup = e.groupby(["company_id", "account_hash", "amount_cents", "effective_entry_date"]).entry_id.transform("size")
    e["is_duplicate_submission"] = dup > 1
    if not returns.empty:
        r = returns[["original_entry_id", "return_class"]].rename(columns={"original_entry_id": "entry_id"})
        e = e.merge(r, on="entry_id", how="left")
    else:
        e["return_class"] = None
    e["is_returned"] = e.return_class.notna()
    for cls in ("unauthorized", "administrative", "nsf", "other"):
        e[f"{cls}_ret"] = (e.return_class == cls).astype(int)

    g = e.groupby(["company_id", "effective_entry_date"])
    daily = g.agg(
        entry_count=("entry_id", "size"),
        file_count=("file_id", "nunique"),
        debit_count=("is_debit", "sum"),
        return_count=("is_returned", "sum"),
        unauthorized_return_count=("unauthorized_ret", "sum"),
        admin_return_count=("administrative_ret", "sum"),
        nsf_return_count=("nsf_ret", "sum"),
        duplicate_entry_count=("is_duplicate_submission", "sum"),
        avg_settlement_lag_days=("settlement_lag_days", "mean"),
    ).reset_index()

    daily["unauthorized_return_rate"] = daily.unauthorized_return_count / daily.entry_count
    daily["admin_return_rate"] = daily.admin_return_count / daily.entry_count
    daily["nsf_return_rate"] = daily.nsf_return_count / daily.entry_count
    daily["overall_return_rate"] = daily.return_count / daily.entry_count
    daily["duplicate_ratio"] = daily.duplicate_entry_count / daily.entry_count
    daily = daily.sort_values(["company_id", "effective_entry_date"]).reset_index(drop=True)

    # Trailing 14 rows, excluding the current row -- same as the SQL window.
    def trailing(col: str, fn: str) -> pd.Series:
        return daily.groupby("company_id")[col].transform(lambda s: getattr(s.shift(1).rolling(14, min_periods=1), fn)())

    daily["baseline_days"] = daily.groupby("company_id").cumcount().clip(upper=14)
    daily["baseline_entry_count"] = trailing("entry_count", "mean")
    daily["baseline_unauthorized_return_rate"] = trailing("unauthorized_return_rate", "mean")
    daily["baseline_admin_return_rate"] = trailing("admin_return_rate", "mean")
    daily["baseline_nsf_return_rate"] = trailing("nsf_return_rate", "mean")
    daily["baseline_settlement_lag_days"] = trailing("avg_settlement_lag_days", "mean")
    daily["baseline_entry_sum"] = trailing("entry_count", "sum")
    daily["baseline_unauthorized_sum"] = trailing("unauthorized_return_count", "sum")
    daily["baseline_admin_sum"] = trailing("admin_return_count", "sum")
    daily["baseline_nsf_sum"] = trailing("nsf_return_count", "sum")
    return daily
