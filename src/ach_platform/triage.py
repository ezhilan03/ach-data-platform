"""LLM triage of detected anomaly clusters -- with a rules baseline to beat.

What the LLM is asked to do: read an evidence packet (the originator's context,
the flagged days' metrics against baseline, the return codes that arrived) and
classify the incident, then write the root-cause hypothesis and the recommended
action an operations analyst would want in the ticket.

What the LLM is deliberately *not* told: which detector signal fired. The rules
baseline (`rules_triage`) gets that for free, which is exactly why it exists --
if the model can't match a six-line lookup table when reading the raw numbers,
the narrative it writes is not to be trusted either. The eval harness reports
both, side by side, against the ground-truth incident plan.

Output is validated with pydantic and retried once with the validation error
in the prompt. No self-reported confidence field: Project 2 measured that LM
confidence was *inversely* related to correctness, so we don't collect it.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from ach_platform.anomaly import AnomalyCluster
from ach_platform.config import Settings
from ach_platform.nacha import RETURN_CODES

IncidentType = Literal[
    "unauthorized_return_spike",
    "admin_return_spike",
    "nsf_spike",
    "volume_drop",
    "duplicate_file",
    "settlement_delay",
    "no_incident",
]

SIGNAL_TO_TYPE = {
    "unauthorized_rate": "unauthorized_return_spike",
    "admin_rate": "admin_return_spike",
    "nsf_rate": "nsf_spike",
    "volume_drop": "volume_drop",
    "duplicate_ratio": "duplicate_file",
    "settlement_lag": "settlement_delay",
}


class TriageResult(BaseModel):
    incident_type: IncidentType
    severity: Literal["low", "medium", "high"]
    root_cause_hypothesis: str = Field(min_length=10, max_length=1200)
    recommended_action: str = Field(min_length=10, max_length=1200)
    evidence_cited: list[str] = Field(default_factory=list, max_length=8)


SYSTEM_PROMPT = """You are an ACH operations analyst at an ODFI. You triage anomalies in an originator's
daily ACH activity. You will receive an evidence packet as JSON and must respond with ONLY a JSON
object (no prose, no markdown) matching this schema:

{
  "incident_type": one of ["unauthorized_return_spike", "admin_return_spike", "nsf_spike",
                           "volume_drop", "duplicate_file", "settlement_delay", "no_incident"],
  "severity": one of ["low", "medium", "high"],
  "root_cause_hypothesis": string (2-4 sentences, specific to the numbers you were given),
  "recommended_action": string (2-4 sentences, concrete next steps for the ODFI/originator),
  "evidence_cited": list of up to 8 short strings quoting the specific numbers that drove your call
}

Definitions (public NACHA context):
- unauthorized_return_spike: returns with codes R05/R07/R10/R11/R29 rise sharply; NACHA's
  unauthorized return-rate threshold is 0.5% of debits. Usually authorization or fraud problems.
- admin_return_spike: R02/R03/R04 (closed / no account / invalid account number) rise sharply;
  NACHA threshold 3.0%. Usually bad account data, e.g. a broken field mapping or stale customer file.
- nsf_spike: R01/R09 (insufficient / uncollected funds) rise sharply. A collectability/credit signal,
  not a rules violation; often seasonal or a change in debit timing.
- volume_drop: originated entry count collapses versus the originator's own baseline. Usually a
  missed or partial file submission.
- duplicate_file: the same entries appear more than once on the same effective date, typically
  with two files from the originator that day. Usually a file resubmitted by mistake.
- settlement_delay: entries settle later than their usual lag after the effective entry date.
- no_incident: the numbers do not support any of the above (noise, tiny volume, or a benign change).

Severity guide: high = network threshold breached or customer funds at risk (duplicates,
unauthorized); medium = operational problem with no rules exposure; low = minor or self-correcting.
Be specific. Cite the numbers. Do not invent data that is not in the packet."""


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def _clean(v):
    if isinstance(v, float):
        if pd.isna(v):
            return None
        return round(v, 5)
    if isinstance(v, (pd.Timestamp, date)):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy scalars
        return _clean(v.item())
    return v


def build_evidence(
    c: AnomalyCluster,
    daily: pd.DataFrame,
    return_codes: pd.DataFrame,
    originator: dict,
) -> dict:
    """Assemble what the LLM sees. Signal names are intentionally absent."""
    daily = daily.copy()
    daily["effective_entry_date"] = pd.to_datetime(daily["effective_entry_date"]).dt.date
    window = daily[
        (daily.company_id == c.company_id)
        & (daily.effective_entry_date >= c.start_date)
        & (daily.effective_entry_date <= c.end_date)
    ]
    prior = daily[(daily.company_id == c.company_id) & (daily.effective_entry_date < c.start_date)].tail(10)

    cols = [
        "effective_entry_date",
        "entry_count",
        "file_count",
        "unauthorized_return_rate",
        "admin_return_rate",
        "nsf_return_rate",
        "overall_return_rate",
        "duplicate_ratio",
        "avg_settlement_lag_days",
        "baseline_entry_count",
        "baseline_unauthorized_return_rate",
        "baseline_admin_return_rate",
        "baseline_nsf_return_rate",
        "baseline_settlement_lag_days",
    ]
    cols = [col for col in cols if col in window.columns]

    rc = return_codes.copy()
    rc["return_date"] = pd.to_datetime(rc["return_date"]).dt.date
    rc_window = rc[
        (rc.company_id == c.company_id)
        & (rc.return_date >= c.start_date)
        & (rc.return_date <= c.end_date + timedelta(days=14))
    ]
    top_codes = (
        rc_window.groupby("return_code")["return_count"].sum().sort_values(ascending=False).head(6)
        if not rc_window.empty
        else pd.Series(dtype="int64")
    )

    return {
        "originator": {k: _clean(v) for k, v in originator.items()},
        "window": {
            "start": c.start_date.isoformat(),
            "end": c.end_date.isoformat(),
            "days_flagged": len({a.day for a in c.anomalies}),
        },
        "flagged_days": [{col: _clean(r[col]) for col in cols} for _, r in window.iterrows()],
        "prior_days_summary": {
            "days": len(prior),
            "avg_entry_count": _clean(prior["entry_count"].mean()) if not prior.empty else None,
            "avg_unauthorized_return_rate": _clean(prior["unauthorized_return_rate"].mean())
            if not prior.empty
            else None,
            "avg_admin_return_rate": _clean(prior["admin_return_rate"].mean()) if not prior.empty else None,
            "avg_nsf_return_rate": _clean(prior["nsf_return_rate"].mean()) if not prior.empty else None,
            "avg_settlement_lag_days": _clean(prior["avg_settlement_lag_days"].mean()) if not prior.empty else None,
        },
        "return_codes_arriving_in_window": [
            {"code": code, "description": RETURN_CODES.get(code, ("?", "?"))[0], "count": int(n)}
            for code, n in top_codes.items()
        ],
        "nacha_thresholds": {"unauthorized": 0.005, "administrative": 0.03, "overall": 0.15},
    }


# --------------------------------------------------------------------------- #
# Rules baseline
# --------------------------------------------------------------------------- #


def rules_triage(c: AnomalyCluster) -> TriageResult:
    itype = SIGNAL_TO_TYPE[c.dominant_signal]
    severity = "high" if itype in ("unauthorized_return_spike", "duplicate_file", "admin_return_spike") else "medium"
    return TriageResult(
        incident_type=itype,  # type: ignore[arg-type]
        severity=severity,
        root_cause_hypothesis=(
            f"Rules baseline: dominant detector signal '{c.dominant_signal}' "
            f"over {len(c.anomalies)} flagged day-signals."
        ),
        recommended_action="Rules baseline does not produce an action; see LLM triage.",
        evidence_cited=[a.detail for a in c.anomalies[:4]],
    )


# --------------------------------------------------------------------------- #
# LLM triage
# --------------------------------------------------------------------------- #

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class TriageCallFailed(RuntimeError):
    """The model could not be reached or did not answer. Carries the meta so the
    run record keeps attempt counts and latencies instead of just a string."""

    def __init__(self, message: str, meta: dict):
        super().__init__(message)
        self.meta = meta


def extract_json(text: str) -> dict:
    """Tolerant JSON extraction: strips reasoning tags and code fences, then takes
    the outermost {...}. Raises ValueError if nothing parseable is there."""
    text = _THINK_RE.sub("", text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _complete(messages: list[dict], settings: Settings) -> str:
    import litellm

    kwargs: dict = {
        "model": settings.triage_model,
        "messages": messages,
        "temperature": 0,
        "timeout": settings.triage_timeout_s,
    }
    if settings.triage_model.startswith(("ollama/", "ollama_chat/")):
        kwargs["api_base"] = settings.ollama_api_base
    if settings.triage_use_json_format:
        try:
            resp = litellm.completion(**kwargs, response_format={"type": "json_object"})
        except Exception:
            # Not every provider/model accepts response_format; the prompt already
            # demands JSON, so fall back to a plain completion.
            resp = litellm.completion(**kwargs)
    else:
        resp = litellm.completion(**kwargs)
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise RuntimeError("triage response truncated (finish_reason=length)")
    return choice.message.content or ""


def llm_triage(evidence: dict, settings: Settings, max_attempts: int = 2) -> tuple[TriageResult, dict]:
    """Returns (result, meta). meta records attempts and raw text for the eval log."""
    user_content = "Evidence packet:\n" + json.dumps(evidence, indent=2)
    if settings.triage_disable_thinking:
        user_content += "\n\n/no_think"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    meta: dict = {
        "model": settings.triage_model,
        "attempts": 0,
        "errors": [],
        "thinking_disabled": settings.triage_disable_thinking,
        "timeout_s": settings.triage_timeout_s,
    }
    last_error: Exception | None = None
    for _ in range(max_attempts):
        meta["attempts"] += 1
        started = time.monotonic()
        try:
            raw = _complete(messages, settings)
        except Exception as exc:
            # A transport failure (timeout, connection refused) is as much a
            # result as a malformed one, and the meta that records it must
            # survive -- the first real run lost every attempt count because
            # this propagated straight out.
            meta.setdefault("latencies_s", []).append(round(time.monotonic() - started, 1))
            meta["errors"].append(f"{type(exc).__name__}: {exc}"[:500])
            raise TriageCallFailed(str(exc)[:500], meta) from exc
        meta.setdefault("latencies_s", []).append(round(time.monotonic() - started, 1))
        meta["raw"] = raw
        try:
            result = TriageResult.model_validate(extract_json(raw))
            return result, meta
        except (ValueError, ValidationError) as exc:
            last_error = exc
            meta["errors"].append(str(exc)[:500])
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": f"Your response was not valid. Error: {exc}\nRespond again with ONLY the JSON object.",
                }
            )
    raise RuntimeError(f"triage failed after {max_attempts} attempts: {last_error}")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def load_context(settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict]]:
    import duckdb

    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        daily = con.execute("SELECT * FROM marts.fct_originator_daily").df()
        codes = con.execute("SELECT * FROM marts.fct_return_codes_daily").df()
        dims = con.execute(
            "SELECT company_id, company_name, industry, primary_sec_code,"
            " base_daily_volume, lifetime_return_rate FROM marts.dim_originators"
        ).df()
    finally:
        con.close()
    originators = {r["company_id"]: {k: _clean(v) for k, v in r.items()} for r in dims.to_dict("records")}
    return daily, codes, originators


def triage_clusters(clusters: list[AnomalyCluster], settings: Settings, use_llm: bool = True) -> Path:
    daily, codes, originators = load_context(settings)
    records = []
    for c in clusters:
        evidence = build_evidence(c, daily, codes, originators.get(c.company_id, {"company_id": c.company_id}))
        record = {
            "cluster": c.to_json(),
            "evidence": evidence,
            "rules": rules_triage(c).model_dump(),
            "llm": None,
            # "skipped" distinguishes "no model was configured" from "the model
            # was asked and produced nothing usable". Conflating them reports a
            # rules-only CI run as 11 model failures.
            "llm_meta": None if use_llm else {"skipped": True},
        }
        if use_llm:
            try:
                result, meta = llm_triage(evidence, settings)
                record["llm"], record["llm_meta"] = result.model_dump(), meta
                print(
                    f"  [{len(records) + 1}/{len(clusters)}] {c.company_id} -> {result.incident_type} "
                    f"({meta.get('latencies_s')}s)",
                    flush=True,
                )
            except Exception as exc:  # keep going; the eval reports failures explicitly
                meta = getattr(exc, "meta", None) or {}
                record["llm_meta"] = {**meta, "error": str(exc)[:500]}
                print(
                    f"  [{len(records) + 1}/{len(clusters)}] {c.company_id} -> FAILED: "
                    f"{type(exc).__name__} after {meta.get('latencies_s')}s",
                    flush=True,
                )
        records.append(record)

    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    out = settings.runs_dir / "triage.json"
    out.write_text(json.dumps({"model": settings.triage_model if use_llm else None, "records": records}, indent=2))
    return out
