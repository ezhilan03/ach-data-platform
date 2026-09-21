from datetime import date

import pandas as pd
import pytest
from pydantic import ValidationError

from ach_platform.anomaly import Anomaly, AnomalyCluster
from ach_platform.triage import (
    SYSTEM_PROMPT,
    TriageResult,
    build_evidence,
    extract_json,
    rules_triage,
)


def _cluster():
    d = date(2026, 8, 3)
    return AnomalyCluster(
        "ORIG0001:2026-08-03",
        "ORIG0001",
        d,
        d,
        [
            Anomaly("ORIG0001", d, "duplicate_ratio", 0.5, 0.0, 0.5, "50% dup"),
            Anomaly("ORIG0001", d, "volume_drop", 10, 200, 0.05, "vol"),
            Anomaly("ORIG0001", d, "duplicate_ratio", 0.5, 0.0, 0.5, "50% dup again"),
        ],
    )


def test_extract_json_strips_thinking_and_fences():
    raw = '<think>hmm\nlots of thoughts</think>\n```json\n{"a": 1, "b": "x"}\n```'
    assert extract_json(raw) == {"a": 1, "b": "x"}


def test_extract_json_tolerates_prose_around_object():
    assert extract_json('Sure! Here it is: {"incident_type": "no_incident"} hope that helps') == {
        "incident_type": "no_incident"
    }


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_schema_rejects_unknown_incident_type_and_confidence_field_is_absent():
    with pytest.raises(ValidationError):
        TriageResult(
            incident_type="fraud",
            severity="high",
            root_cause_hypothesis="x" * 20,
            recommended_action="y" * 20,
        )
    assert "confidence" not in TriageResult.model_fields


def test_rules_triage_uses_dominant_signal():
    r = rules_triage(_cluster())
    assert r.incident_type == "duplicate_file"
    assert r.severity == "high"


def test_evidence_packet_hides_signal_names():
    c = _cluster()
    daily = pd.DataFrame(
        [
            dict(
                company_id="ORIG0001",
                effective_entry_date=date(2026, 7, 31),
                entry_count=200,
                file_count=1,
                unauthorized_return_rate=0.001,
                admin_return_rate=0.004,
                nsf_return_rate=0.01,
                overall_return_rate=0.015,
                duplicate_ratio=0.0,
                avg_settlement_lag_days=1.0,
                baseline_entry_count=200.0,
                baseline_unauthorized_return_rate=0.001,
                baseline_admin_return_rate=0.004,
                baseline_nsf_return_rate=0.01,
                baseline_settlement_lag_days=1.0,
            ),
            dict(
                company_id="ORIG0001",
                effective_entry_date=date(2026, 8, 3),
                entry_count=400,
                file_count=2,
                unauthorized_return_rate=0.001,
                admin_return_rate=0.004,
                nsf_return_rate=0.01,
                overall_return_rate=0.015,
                duplicate_ratio=1.0,
                avg_settlement_lag_days=1.0,
                baseline_entry_count=200.0,
                baseline_unauthorized_return_rate=0.001,
                baseline_admin_return_rate=0.004,
                baseline_nsf_return_rate=0.01,
                baseline_settlement_lag_days=1.0,
            ),
        ]
    )
    codes = pd.DataFrame(
        [
            dict(
                return_date=date(2026, 8, 5),
                company_id="ORIG0001",
                return_code="R01",
                return_class="nsf",
                return_count=3,
            )
        ]
    )
    ev = build_evidence(c, daily, codes, {"company_id": "ORIG0001", "industry": "utilities"})
    text = str(ev)
    for hidden in ('duplicate_ratio": 0.5', "dominant", "signal", "50% dup"):
        assert hidden not in text
    assert ev["flagged_days"][0]["file_count"] == 2
    assert ev["flagged_days"][0]["duplicate_ratio"] == 1.0
    assert ev["prior_days_summary"]["days"] == 1
    assert ev["return_codes_arriving_in_window"][0]["code"] == "R01"


def test_prompt_never_mentions_the_detector():
    # The model must reason from the numbers, not from which rule fired.
    for leak in ("detector", "flagged by", "rule fired", "dominant"):
        assert leak not in SYSTEM_PROMPT.lower()
