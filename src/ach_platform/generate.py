"""Deterministic synthetic ACH generator with labeled incident injection.

Design goals (each one is load-bearing for the rest of the platform):

1. **Per-day idempotency.** `day_bundle(D)` depends only on (seed, D). Re-running
   an Airflow task for a day rewrites byte-identical Parquet, so backfills and
   retries are safe. No global counters, no `uuid4()`, no `hash()` -- all of
   those silently break reproducibility (a lesson from the recon-engine repo).

2. **Returns arrive late, like real ACH.** A return for an entry originated on
   day P shows up 1-10 banking days later. The ingest for day D therefore lands
   the *returns that arrive on D*, which belong to entries from earlier days.
   Return rates for a day are only fully known after the return window closes.

3. **Labeled incidents are the ground truth.** The incident plan is a pure
   function of the seed. The generator injects them; the detector and the LLM
   triage never see the plan. That's what makes precision/recall measurable.

Only public NACHA structure is modeled (SEC codes, transaction codes, R-codes,
return-rate thresholds). Originators, accounts, and amounts are synthetic.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from functools import lru_cache
from random import Random

from ach_platform.nacha import CODES_BY_CLASS, RETURN_CLASSES

# Fixed namespace so uuid5() ids are stable across runs and machines.
NAMESPACE = uuid.UUID("6f1c7a2e-4c10-4d0c-9f3e-000000000001")

INCIDENT_TYPES = (
    "unauthorized_return_spike",
    "admin_return_spike",
    "nsf_spike",
    "volume_drop",
    "duplicate_file",
    "settlement_delay",
)

# Industry archetypes: (sec_code, debit_share, base_daily_volume, amount_mu, amount_sigma,
#                       base return rates by class)
# Rates are per-entry probabilities. Baselines sit comfortably under the NACHA
# thresholds (0.5% unauthorized, 3% administrative) so that incidents are the
# only thing that crosses them.
INDUSTRIES: dict[str, dict] = {
    "utilities": dict(sec="PPD", debit_share=0.98, volume=220, mu=4.6, sigma=0.5,
                      rates=dict(unauthorized=0.0006, administrative=0.004, nsf=0.010, other=0.0005)),
    "subscription_media": dict(sec="WEB", debit_share=0.99, volume=300, mu=2.7, sigma=0.3,
                               rates=dict(unauthorized=0.0020, administrative=0.006, nsf=0.018, other=0.0008)),
    "payroll": dict(sec="PPD", debit_share=0.03, volume=180, mu=7.2, sigma=0.4,
                    rates=dict(unauthorized=0.0001, administrative=0.003, nsf=0.0005, other=0.0003)),
    "consumer_lending": dict(sec="WEB", debit_share=0.97, volume=140, mu=5.5, sigma=0.4,
                             rates=dict(unauthorized=0.0018, administrative=0.005, nsf=0.030, other=0.0010)),
    "insurance": dict(sec="PPD", debit_share=0.95, volume=160, mu=5.1, sigma=0.35,
                      rates=dict(unauthorized=0.0008, administrative=0.004, nsf=0.012, other=0.0006)),
    "b2b_supplier": dict(sec="CCD", debit_share=0.60, volume=60, mu=8.0, sigma=0.7,
                         rates=dict(unauthorized=0.0004, administrative=0.006, nsf=0.008, other=0.0010)),
    "fitness": dict(sec="PPD", debit_share=0.99, volume=120, mu=3.6, sigma=0.2,
                    rates=dict(unauthorized=0.0025, administrative=0.007, nsf=0.020, other=0.0008)),
    "telecom_collections": dict(sec="TEL", debit_share=0.99, volume=90, mu=4.3, sigma=0.5,
                                rates=dict(unauthorized=0.0030, administrative=0.010, nsf=0.035, other=0.0012)),
}

ROUTING_POOL_SIZE = 40
DOW_FACTOR = {0: 1.15, 1: 1.05, 2: 1.0, 3: 0.95, 4: 1.10}  # Mon..Fri


@dataclass(frozen=True)
class Originator:
    company_id: str
    company_name: str
    industry: str
    sec_code: str
    odfi_routing: str
    base_daily_volume: int
    debit_share: float
    amount_mu: float
    amount_sigma: float
    customer_pool_size: int
    rate_unauthorized: float
    rate_administrative: float
    rate_nsf: float
    rate_other: float

    def base_rates(self) -> dict[str, float]:
        return {
            "unauthorized": self.rate_unauthorized,
            "administrative": self.rate_administrative,
            "nsf": self.rate_nsf,
            "other": self.rate_other,
        }


@dataclass(frozen=True)
class Incident:
    incident_id: str
    company_id: str
    incident_type: str
    start_date: date
    end_date: date
    magnitude: float
    description: str

    def active_on(self, d: date) -> bool:
        return self.start_date <= d <= self.end_date

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["start_date"] = self.start_date.isoformat()
        payload["end_date"] = self.end_date.isoformat()
        return payload

    @classmethod
    def from_json(cls, payload: dict) -> Incident:
        return cls(
            incident_id=payload["incident_id"],
            company_id=payload["company_id"],
            incident_type=payload["incident_type"],
            start_date=date.fromisoformat(payload["start_date"]),
            end_date=date.fromisoformat(payload["end_date"]),
            magnitude=float(payload["magnitude"]),
            description=payload["description"],
        )


# --------------------------------------------------------------------------- #
# Calendar helpers (banking days = Mon-Fri; federal holidays deliberately ignored)
# --------------------------------------------------------------------------- #

def is_banking_day(d: date) -> bool:
    return d.weekday() < 5


def add_banking_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if is_banking_day(d):
            n -= 1
    return d


def banking_days_between(start: date, end: date) -> list[date]:
    """Inclusive range of banking days."""
    out, d = [], start
    while d <= end:
        if is_banking_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- #
# Static dimensions: originators and the incident plan
# --------------------------------------------------------------------------- #

def _stable_id(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts)))


def build_originators(seed: int, n: int = 24) -> list[Originator]:
    rng = Random(f"{seed}:originators")
    industries = list(INDUSTRIES)
    originators: list[Originator] = []
    for i in range(n):
        industry = industries[i % len(industries)]
        spec = INDUSTRIES[industry]
        # Every originator is a jittered copy of its archetype so the population
        # has spread without any two originators being identical.
        jitter = rng.uniform(0.7, 1.4)
        rates = {k: v * rng.uniform(0.6, 1.5) for k, v in spec["rates"].items()}
        originators.append(
            Originator(
                company_id=f"ORIG{i + 1:04d}",
                company_name=f"{industry.replace('_', ' ').title()} Co {i + 1:02d}",
                industry=industry,
                sec_code=spec["sec"],
                odfi_routing=f"0{rng.randint(1000000, 9999999)}",
                base_daily_volume=max(20, int(spec["volume"] * jitter)),
                debit_share=spec["debit_share"],
                amount_mu=spec["mu"],
                amount_sigma=spec["sigma"],
                customer_pool_size=max(200, int(spec["volume"] * jitter * 12)),
                rate_unauthorized=rates["unauthorized"],
                rate_administrative=rates["administrative"],
                rate_nsf=rates["nsf"],
                rate_other=rates["other"],
            )
        )
    return originators


_INCIDENT_SPECS = {
    # type: (min_days, max_days, magnitude range, description)
    "unauthorized_return_spike": (3, 5, (0.015, 0.04), "Unauthorized returns (R05/R07/R10/R11/R29) spike to {m:.1%} of entries"),
    "admin_return_spike": (3, 5, (0.05, 0.12), "Administrative returns (R02/R03/R04) spike to {m:.1%} of entries"),
    "nsf_spike": (3, 5, (0.06, 0.12), "NSF returns (R01/R09) spike to {m:.1%} of entries"),
    "volume_drop": (2, 3, (0.85, 0.95), "Originated volume drops by {m:.0%} (missed/partial file)"),
    "duplicate_file": (1, 2, (1.0, 1.0), "Day's file submitted twice -- every entry duplicated"),
    "settlement_delay": (2, 3, (2.0, 2.0), "Settlement lags effective date by an extra {m:.0f} banking days"),
}


def plan_incidents(
    seed: int,
    originators: list[Originator],
    history_start: date,
    history_end: date,
    n_incidents: int = 10,
) -> list[Incident]:
    """One incident per affected originator, placed so a baseline exists before it
    and the return window has matured after it."""
    rng = Random(f"{seed}:incidents")
    # 16 days of baseline before, and 16 days after so the return window has
    # closed (unauthorized returns lag up to 10 banking days + settlement).
    earliest_start = history_start + timedelta(days=16)
    latest_end = history_end - timedelta(days=16)
    if earliest_start >= latest_end:
        return []  # history too short for labeled incidents (e.g. tiny CI runs)

    incidents: list[Incident] = []
    types = list(INCIDENT_TYPES)
    pool = list(originators)
    rng.shuffle(pool)
    for i in range(min(n_incidents, len(originators))):
        # Cycle through types so every type appears at least once for n >= 6.
        itype = types[i % len(types)]
        # A return spike is only meaningful for a debit originator: credits
        # (payroll) almost never return and can't NSF. Picking a payroll shop
        # for an NSF spike would produce a labeled incident with no signal --
        # which the first eval run duly reported as two undetectable incidents.
        needs_debits = itype.endswith("_spike")
        candidates = [o for o in pool if (o.debit_share >= 0.5 or not needs_debits)]
        if not candidates:
            break
        originator = candidates[0]
        pool.remove(originator)
        min_d, max_d, (lo, hi), template = _INCIDENT_SPECS[itype]
        duration = rng.randint(min_d, max_d)
        magnitude = rng.uniform(lo, hi)
        # Pick a start whose whole span stays inside the allowed window.
        span = (latest_end - earliest_start).days - duration
        start = earliest_start + timedelta(days=rng.randint(0, max(span, 0)))
        while not is_banking_day(start):
            start += timedelta(days=1)
        end = start + timedelta(days=duration - 1)
        incidents.append(
            Incident(
                incident_id=f"INC{i + 1:03d}",
                company_id=originator.company_id,
                incident_type=itype,
                start_date=start,
                end_date=end,
                magnitude=magnitude,
                description=template.format(m=magnitude),
            )
        )
    return sorted(incidents, key=lambda x: x.start_date)


def incident_for(incidents: list[Incident], company_id: str, d: date) -> Incident | None:
    for inc in incidents:
        if inc.company_id == company_id and inc.active_on(d):
            return inc
    return None


# --------------------------------------------------------------------------- #
# Per-day generation
# --------------------------------------------------------------------------- #

def _account_hash(company_id: str, customer_idx: int) -> str:
    return hashlib.sha256(f"{company_id}:{customer_idx}".encode()).hexdigest()[:32]


def _entries_for_file(
    rng: Random,
    originator: Originator,
    d: date,
    incident: Incident | None,
    file_seq: int,
    n_entries: int,
    trace_start: int,
    routing_pool: list[str],
) -> list[dict]:
    settlement_lag = 1
    if incident and incident.incident_type == "settlement_delay":
        settlement_lag += int(incident.magnitude)
    settlement_date = add_banking_days(d, settlement_lag)
    batch_id = _stable_id("batch", originator.company_id, d, file_seq)
    file_id = _stable_id("file", originator.company_id, d, file_seq)

    rows = []
    for i in range(n_entries):
        customer_idx = rng.randrange(originator.customer_pool_size)
        is_debit = rng.random() < originator.debit_share
        is_savings = rng.random() < 0.15
        tx_code = ("37" if is_savings else "27") if is_debit else ("32" if is_savings else "22")
        amount_cents = max(100, int(round(rng.lognormvariate(originator.amount_mu, originator.amount_sigma) * 100)))
        rows.append(
            {
                "entry_id": _stable_id("entry", originator.company_id, d, file_seq, i),
                "file_id": file_id,
                "batch_id": batch_id,
                "trace_number": f"{originator.odfi_routing}{(trace_start + i) % 10_000_000:07d}",
                "company_id": originator.company_id,
                "sec_code": originator.sec_code,
                "transaction_code": tx_code,
                "amount_cents": amount_cents,
                "effective_entry_date": d,
                "settlement_date": settlement_date,
                "receiving_dfi_id": rng.choice(routing_pool),
                "account_hash": _account_hash(originator.company_id, customer_idx),
                "customer_idx": customer_idx,
            }
        )
    return rows


def _draw_return(entry: dict, originator: Originator, incident: Incident | None, seed: int) -> dict | None:
    """Decide once, deterministically, whether an entry returns and when."""
    rng = Random(f"{seed}:return:{entry['entry_id']}")
    rates = originator.base_rates()
    if incident:
        target = {
            "unauthorized_return_spike": "unauthorized",
            "admin_return_spike": "administrative",
            "nsf_spike": "nsf",
        }.get(incident.incident_type)
        if target:
            rates[target] = max(rates[target], incident.magnitude)
    is_credit = entry["transaction_code"] in ("22", "32")
    if is_credit:
        # Credits rarely return; NSF is impossible for a credit.
        rates = {k: v * 0.15 for k, v in rates.items()}
        rates["nsf"] = 0.0

    u = rng.random()
    cumulative = 0.0
    return_class = None
    for cls in RETURN_CLASSES:
        cumulative += rates[cls]
        if u < cumulative:
            return_class = cls
            break
    if return_class is None:
        return None

    code = rng.choice(CODES_BY_CLASS[return_class])
    lag = {
        "nsf": rng.randint(1, 2),
        "administrative": rng.randint(1, 2),
        "unauthorized": rng.randint(1, 10),
        "other": rng.randint(1, 3),
    }[return_class]
    return {
        "return_id": _stable_id("return", entry["entry_id"]),
        "original_trace_number": entry["trace_number"],
        "original_entry_id": entry["entry_id"],
        "company_id": entry["company_id"],
        "return_code": code,
        "return_class": return_class,
        "amount_cents": entry["amount_cents"],
        "original_effective_date": entry["effective_entry_date"],
        "return_date": add_banking_days(entry["settlement_date"], lag),
    }


@dataclass
class DayBundle:
    day: date
    entries: list[dict]
    files: list[dict]
    returns: list[dict]  # returns *originating* from this day's entries (arrive later)


class Generator:
    """Holds the static dimensions and memoizes per-day bundles."""

    def __init__(self, seed: int, history_start: date, history_end: date, n_originators: int = 24):
        self.seed = seed
        self.history_start = history_start
        self.history_end = history_end
        self.originators = build_originators(seed, n_originators)
        self.incidents = plan_incidents(seed, self.originators, history_start, history_end)
        rng = Random(f"{seed}:routing")
        self.routing_pool = [f"{rng.randint(10000000, 99999999):08d}" for _ in range(ROUTING_POOL_SIZE)]
        self._by_id = {o.company_id: o for o in self.originators}
        self.day_bundle = lru_cache(maxsize=128)(self._day_bundle)

    def _day_bundle(self, d: date) -> DayBundle:
        entries: list[dict] = []
        files: list[dict] = []
        returns: list[dict] = []
        if not is_banking_day(d):
            return DayBundle(d, entries, files, returns)

        rng = Random(f"{self.seed}:{d.isoformat()}")
        for originator in self.originators:
            incident = incident_for(self.incidents, originator.company_id, d)
            volume = originator.base_daily_volume * DOW_FACTOR[d.weekday()] * rng.lognormvariate(0, 0.15)
            if incident and incident.incident_type == "volume_drop":
                volume *= 1.0 - incident.magnitude
            n = max(0, int(round(volume)))
            n_files = 2 if (incident and incident.incident_type == "duplicate_file") else 1

            # Trace sequence numbers restart per originator per day (they only need
            # to be unique within a file), offset by day so files never collide.
            trace_start = ((d.toordinal() * 1000) % 9_000_000) + 1
            file_rows = _entries_for_file(rng, originator, d, incident, 1, n, trace_start, self.routing_pool)
            all_rows = list(file_rows)
            if n_files == 2:
                # A duplicate submission: same entries, new file/batch/trace/entry ids.
                dup_file_id = _stable_id("file", originator.company_id, d, 2)
                dup_batch_id = _stable_id("batch", originator.company_id, d, 2)
                for i, row in enumerate(file_rows):
                    dup = dict(row)
                    dup["entry_id"] = _stable_id("entry", originator.company_id, d, 2, i)
                    dup["file_id"] = dup_file_id
                    dup["batch_id"] = dup_batch_id
                    dup["trace_number"] = f"{originator.odfi_routing}{(trace_start + n + i) % 10_000_000:07d}"
                    all_rows.append(dup)

            for file_seq in range(1, n_files + 1):
                file_id = _stable_id("file", originator.company_id, d, file_seq)
                rows = [r for r in all_rows if r["file_id"] == file_id]
                files.append(
                    {
                        "file_id": file_id,
                        "company_id": originator.company_id,
                        "file_creation_date": d,
                        "file_sequence": file_seq,
                        "batch_count": 1,
                        "entry_count": len(rows),
                        "total_debit_cents": sum(r["amount_cents"] for r in rows if r["transaction_code"] in ("27", "37")),
                        "total_credit_cents": sum(r["amount_cents"] for r in rows if r["transaction_code"] in ("22", "32")),
                    }
                )

            for row in all_rows:
                ret = _draw_return(row, originator, incident, self.seed)
                if ret:
                    returns.append(ret)
            entries.extend(all_rows)

        # Strip generator-internal fields before the rows become "raw data".
        for row in entries:
            row.pop("customer_idx", None)
        return DayBundle(d, entries, files, returns)

    def returns_arriving_on(self, d: date, lookback_days: int = 16) -> list[dict]:
        """Returns whose return_date == d. Unauthorized returns lag up to 10 banking
        days (+ settlement), so we look back 16 calendar days to be safe."""
        arriving: list[dict] = []
        # Never look before history_start: those days are never landed, so their
        # returns would be orphans in the lake (and fail the relationships test).
        window_start = max(d - timedelta(days=lookback_days), self.history_start)
        for p in banking_days_between(window_start, d - timedelta(days=1)):
            arriving.extend(r for r in self.day_bundle(p).returns if r["return_date"] == d)
        return arriving

    def originator_rows(self) -> list[dict]:
        return [
            {
                "company_id": o.company_id,
                "company_name": o.company_name,
                "industry": o.industry,
                "sec_code": o.sec_code,
                "odfi_routing": o.odfi_routing,
                "base_daily_volume": o.base_daily_volume,
            }
            for o in self.originators
        ]

    def summary(self) -> dict:
        return {
            "seed": self.seed,
            "history_start": self.history_start.isoformat(),
            "history_end": self.history_end.isoformat(),
            "originators": len(self.originators),
            "incidents": len(self.incidents),
            "incident_types": sorted({i.incident_type for i in self.incidents}),
        }


def expected_daily_volume(originator: Originator) -> float:
    """Mean of the un-incident daily volume; handy for tests and docs."""
    return originator.base_daily_volume * sum(DOW_FACTOR.values()) / len(DOW_FACTOR) * math.exp(0.15**2 / 2)
