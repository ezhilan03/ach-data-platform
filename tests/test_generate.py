from datetime import date, timedelta

from ach_platform.generate import Generator, add_banking_days, is_banking_day, plan_incidents

START, END = date(2026, 7, 3), date(2026, 8, 31)


def test_generation_is_deterministic():
    a = Generator(42, START, END).day_bundle(date(2026, 8, 3))
    b = Generator(42, START, END).day_bundle(date(2026, 8, 3))
    assert a.entries == b.entries
    assert a.returns == b.returns
    assert a.files == b.files


def test_different_seed_different_data():
    a = Generator(42, START, END).day_bundle(date(2026, 8, 3))
    b = Generator(43, START, END).day_bundle(date(2026, 8, 3))
    assert a.entries != b.entries


def test_weekends_have_no_entries():
    gen = Generator(42, START, END)
    saturday = date(2026, 8, 1)
    assert not is_banking_day(saturday)
    assert gen.day_bundle(saturday).entries == []


def test_ids_and_traces_unique_across_history():
    gen = Generator(42, START, END)
    ids, traces = set(), set()
    d = START
    while d <= END:
        for e in gen.day_bundle(d).entries:
            assert e["entry_id"] not in ids
            ids.add(e["entry_id"])
            key = (e["file_id"], e["trace_number"])
            assert key not in traces
            traces.add(key)
        d += timedelta(days=1)
    assert len(ids) > 100_000


def test_returns_arrive_after_settlement_and_only_from_landed_days():
    gen = Generator(42, START, END)
    arriving = gen.returns_arriving_on(date(2026, 7, 7))
    assert arriving, "expected some returns a few days into history"
    for r in arriving:
        assert r["return_date"] == date(2026, 7, 7)
        assert r["original_effective_date"] >= START
        assert r["original_effective_date"] < r["return_date"]


def test_incident_plan_covers_every_type_and_respects_windows():
    gen = Generator(42, START, END)
    types = {i.incident_type for i in gen.incidents}
    assert types == {
        "unauthorized_return_spike",
        "admin_return_spike",
        "nsf_spike",
        "volume_drop",
        "duplicate_file",
        "settlement_delay",
    }
    for inc in gen.incidents:
        assert inc.start_date >= START + timedelta(days=16)
        assert inc.end_date <= END - timedelta(days=16)
    # one incident per originator
    assert len({i.company_id for i in gen.incidents}) == len(gen.incidents)


def test_incident_plan_empty_when_history_too_short():
    gen = Generator(42, date(2026, 8, 1), date(2026, 8, 14))
    assert plan_incidents(42, gen.originators, gen.history_start, gen.history_end) == []


def test_duplicate_file_incident_doubles_entries_with_two_files():
    gen = Generator(42, START, END)
    inc = next(i for i in gen.incidents if i.incident_type == "duplicate_file")
    day = inc.start_date
    bundle = gen.day_bundle(day)
    files = [f for f in bundle.files if f["company_id"] == inc.company_id]
    assert len(files) == 2
    entries = [e for e in bundle.entries if e["company_id"] == inc.company_id]
    assert len(entries) == sum(f["entry_count"] for f in files)
    # Every entry appears twice by (account, amount) -- that's the signature.
    keys = [(e["account_hash"], e["amount_cents"]) for e in entries]
    assert len(keys) == 2 * len(set(keys))


def test_settlement_delay_incident_pushes_settlement_out():
    gen = Generator(42, START, END)
    inc = next(i for i in gen.incidents if i.incident_type == "settlement_delay")
    entries = [e for e in gen.day_bundle(inc.start_date).entries if e["company_id"] == inc.company_id]
    normal = add_banking_days(inc.start_date, 1)
    assert all(e["settlement_date"] > normal for e in entries)


def test_volume_drop_incident_reduces_volume():
    gen = Generator(42, START, END)
    inc = next(i for i in gen.incidents if i.incident_type == "volume_drop")
    o = next(o for o in gen.originators if o.company_id == inc.company_id)
    n = len([e for e in gen.day_bundle(inc.start_date).entries if e["company_id"] == inc.company_id])
    assert n < 0.3 * o.base_daily_volume


def test_every_return_lands_exactly_once():
    """The invariant the ingest lookback exists to guarantee.

    A return is generated when its originating entry is generated, but it is
    *landed* on the day it arrives, which the ingest finds by looking back over
    recent days. If that window is too narrow the lake silently drops returns:
    every partition is still well-formed and every dbt test still passes, and
    the only symptom is a return rate that is quietly too low.

    That is exactly what happened -- a 16-day lookback dropped 7 of 4,369
    returns, all of them entries originated on a Friday whose unauthorized
    return arrived 17 calendar days later.
    """
    gen = Generator(42, START, END)
    generated, landed = set(), []
    d = START
    while d <= END:
        generated |= {r["return_id"] for r in gen.day_bundle(d).returns if r["return_date"] <= END}
        landed += [r["return_id"] for r in gen.returns_arriving_on(d)]
        d += timedelta(days=1)

    assert len(landed) == len(set(landed)), "a return was landed in more than one partition"
    missing = generated - set(landed)
    assert not missing, f"{len(missing)} generated returns never land in any partition"


def test_return_lag_stays_inside_the_ingest_lookback():
    """Guards the bound directly, so a change to the lag distribution or to the
    settlement-delay magnitude fails here rather than silently truncating."""
    gen = Generator(42, START, END)
    default_lookback = Generator.returns_arriving_on.__defaults__[0]
    worst = 0
    d = START
    while d <= END:
        for r in gen.day_bundle(d).returns:
            worst = max(worst, (r["return_date"] - r["original_effective_date"]).days)
        d += timedelta(days=1)
    assert worst <= default_lookback, f"worst gap {worst}d exceeds the {default_lookback}d lookback"
