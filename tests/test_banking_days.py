"""Pins the closed-form banking-day formula used by the dbt macro
`banking_days_between` to a brute-force count. If this test passes, the SQL
is correct by construction -- it is the same arithmetic, transcribed."""

from datetime import date, timedelta

from reference_marts import banking_days_between


def _duckdb_dayofweek(d: date) -> int:
    return (d.weekday() + 1) % 7  # DuckDB: 0 = Sunday .. 6 = Saturday


def closed_form(a: date, b: date) -> int:
    n = (b - a).days
    dow = _duckdb_dayofweek(a)
    k_sat = 7 if dow == 6 else 6 - dow
    k_sun = 7 if dow == 0 else 7 - dow
    return n - (n + 7 - k_sat) // 7 - (n + 7 - k_sun) // 7


def test_closed_form_matches_brute_force_for_every_weekday_and_lag():
    for offset in range(14):
        a = date(2026, 7, 1) + timedelta(days=offset)
        for n in range(0, 30):
            b = a + timedelta(days=n)
            assert closed_form(a, b) == banking_days_between(a, b), (a, b)


def test_friday_to_monday_is_one_banking_day():
    assert banking_days_between(date(2026, 7, 31), date(2026, 8, 3)) == 1
    assert closed_form(date(2026, 7, 31), date(2026, 8, 3)) == 1
