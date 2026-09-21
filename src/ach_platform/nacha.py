"""Public NACHA reference data used by the generator, the dbt models, and the detector.

Only the public Operating Rules are encoded here (return reason codes, SEC codes,
transaction codes, and the network return-rate thresholds). Nothing in this
module is employer-specific.
"""

from __future__ import annotations

# Return reason code -> (description, class). The class is what the detector and
# the marts aggregate on; the network thresholds are defined per class.
RETURN_CODES: dict[str, tuple[str, str]] = {
    "R01": ("Insufficient funds", "nsf"),
    "R02": ("Account closed", "administrative"),
    "R03": ("No account / unable to locate account", "administrative"),
    "R04": ("Invalid account number structure", "administrative"),
    "R05": ("Unauthorized debit to consumer account using corporate SEC code", "unauthorized"),
    "R07": ("Authorization revoked by customer", "unauthorized"),
    "R08": ("Payment stopped", "other"),
    "R09": ("Uncollected funds", "nsf"),
    "R10": ("Customer advises originator not known / not authorized", "unauthorized"),
    "R11": (
        "Customer advises entry not in accordance with the terms of authorization",
        "unauthorized",
    ),
    "R16": ("Account frozen", "other"),
    "R20": ("Non-transaction account", "other"),
    "R29": ("Corporate customer advises not authorized", "unauthorized"),
}

RETURN_CLASSES = ("unauthorized", "administrative", "nsf", "other")

CODES_BY_CLASS: dict[str, list[str]] = {
    cls: [code for code, (_, c) in RETURN_CODES.items() if c == cls] for cls in RETURN_CLASSES
}

# Network return-rate thresholds (NACHA Operating Rules). Exceeding these
# triggers an ODFI inquiry; the "overall" threshold counts every return code.
NACHA_THRESHOLDS: dict[str, float] = {
    "unauthorized": 0.005,  # 0.5%
    "administrative": 0.03,  # 3.0%
    "overall": 0.15,  # 15.0%
}

# Standard Entry Class codes used by the synthetic originators.
SEC_CODES = ("PPD", "CCD", "WEB", "TEL")

# Transaction codes: 2x = checking, 3x = savings; x2 = credit, x7 = debit.
TRANSACTION_CODES: dict[str, tuple[str, str]] = {
    "22": ("checking", "credit"),
    "27": ("checking", "debit"),
    "32": ("savings", "credit"),
    "37": ("savings", "debit"),
}

# Typical settlement lag in banking days for a standard (non-Same-Day) entry.
STANDARD_SETTLEMENT_LAG_DAYS = 1
