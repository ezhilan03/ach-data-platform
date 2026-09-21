from datetime import date, timedelta

import pandas as pd

from ach_platform.anomaly import Anomaly, DetectorConfig, cluster, detect, poisson_sf


def _row(company="ORIG0001", day=date(2026, 8, 1), **over):
    # A quiet 200-entry day for an originator whose trailing 14 days pooled
    # 2,800 entries with 3 unauthorized, 14 administrative and 56 NSF returns.
    base = dict(
        company_id=company,
        effective_entry_date=day,
        entry_count=200,
        file_count=1,
        unauthorized_return_count=0,
        admin_return_count=1,
        nsf_return_count=4,
        duplicate_ratio=0.0,
        avg_settlement_lag_days=1.0,
        baseline_days=14,
        baseline_entry_count=200.0,
        baseline_settlement_lag_days=1.0,
        baseline_entry_sum=2800.0,
        baseline_unauthorized_sum=3.0,
        baseline_admin_sum=14.0,
        baseline_nsf_sum=56.0,
    )
    base.update(over)
    return base


AS_OF = date(2026, 9, 1)  # every August day is "matured"


def test_poisson_sf_matches_hand_values():
    assert abs(poisson_sf(3, 0.2) - 0.001148) < 1e-5
    assert abs(poisson_sf(6, 1.0) - 0.000594) < 1e-5
    assert poisson_sf(0, 5.0) == 1.0
    assert poisson_sf(3, 0.0) == 0.0


def test_quiet_day_is_not_flagged():
    assert detect(pd.DataFrame([_row()]), as_of=AS_OF) == []


def test_one_or_two_returns_never_flag_even_when_surprising():
    # p0 ~ 0.1%: two unauthorized returns is p ~ 0.02 -- and anyway below min_return_count.
    assert detect(pd.DataFrame([_row(unauthorized_return_count=2)]), as_of=AS_OF) == []


def test_unauthorized_spike_flags_with_pvalue_in_detail():
    out = detect(pd.DataFrame([_row(unauthorized_return_count=5)]), as_of=AS_OF)
    assert [a.signal for a in out] == ["unauthorized_rate"]
    assert "p=" in out[0].detail and out[0].observed == 0.025


def test_admin_spike_needs_more_events_because_its_baseline_is_higher():
    # baseline p0 ~ 0.44% -> lambda ~ 0.9 on 200 entries; 4 events is p ~ 1.3%, 7 is ~ 1e-4
    assert detect(pd.DataFrame([_row(admin_return_count=4)]), as_of=AS_OF) == []
    assert [a.signal for a in detect(pd.DataFrame([_row(admin_return_count=7)]), as_of=AS_OF)] == ["admin_rate"]


def test_immature_days_skip_rate_signals_but_not_volume():
    day = date(2026, 8, 30)
    row = _row(day=day, unauthorized_return_count=10, entry_count=20, baseline_entry_count=200.0)
    out = detect(pd.DataFrame([row]), as_of=AS_OF)
    assert [a.signal for a in out] == ["volume_drop"]


def test_no_baseline_no_flags():
    row = _row(baseline_days=2, unauthorized_return_count=10, entry_count=10)
    assert detect(pd.DataFrame([row]), as_of=AS_OF) == []


def test_duplicate_and_settlement_signals():
    row = _row(duplicate_ratio=0.5, file_count=2, avg_settlement_lag_days=3.0)
    out = detect(pd.DataFrame([row]), as_of=AS_OF)
    assert sorted(a.signal for a in out) == ["duplicate_ratio", "settlement_lag"]


def test_cluster_groups_consecutive_days_and_splits_on_gaps():
    d0 = date(2026, 8, 3)
    anomalies = [
        Anomaly("A", d0, "admin_rate", 0.1, 0.01, 5.0, ""),
        Anomaly("A", d0 + timedelta(days=1), "admin_rate", 0.1, 0.01, 5.0, ""),
        Anomaly("A", d0 + timedelta(days=4), "admin_rate", 0.1, 0.01, 5.0, ""),  # Fri after Tue: gap 3 -> same cluster
        Anomaly("A", d0 + timedelta(days=10), "volume_drop", 10, 200, 0.05, ""),
        Anomaly("B", d0, "nsf_rate", 0.2, 0.01, 6.0, ""),
    ]
    clusters = cluster(anomalies, max_gap_days=3)
    assert [(c.company_id, c.start_date, c.end_date) for c in clusters] == [
        ("A", d0, d0 + timedelta(days=4)),
        ("A", d0 + timedelta(days=10), d0 + timedelta(days=10)),
        ("B", d0, d0),
    ]
    assert clusters[0].dominant_signal == "admin_rate"


def test_config_is_overridable():
    row = _row(entry_count=90, baseline_entry_count=200.0)  # 45%: not a drop by default
    assert detect(pd.DataFrame([row]), as_of=AS_OF) == []
    assert [a.signal for a in detect(pd.DataFrame([row]), DetectorConfig(volume_drop_ratio=0.5), as_of=AS_OF)] == [
        "volume_drop"
    ]
