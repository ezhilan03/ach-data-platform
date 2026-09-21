"""Grafana annotations: put the triage verdict on the dashboard timeline.

An annotation is the cheapest possible "AI in the loop" surface: the LLM's
classification and hypothesis show up as a marker on the exact days of the
anomaly, next to the metric that triggered it. No new UI, no new service.

No-op when GRAFANA_URL is unset.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import requests

from ach_platform.config import Settings


def _ms(d: date, end_of_day: bool = False) -> int:
    dt = datetime(
        d.year,
        d.month,
        d.day,
        23 if end_of_day else 0,
        59 if end_of_day else 0,
        tzinfo=UTC,
    )
    return int(dt.timestamp() * 1000)


def annotate(settings: Settings, start: date, end: date, text: str, tags: list[str]) -> int | None:
    if not settings.grafana_url:
        return None
    resp = requests.post(
        f"{settings.grafana_url.rstrip('/')}/api/annotations",
        auth=(settings.grafana_user, settings.grafana_password),
        json={"time": _ms(start), "timeEnd": _ms(end, end_of_day=True), "tags": tags, "text": text},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("id")


def annotate_triage(settings: Settings, triage_records: list[dict]) -> int:
    """One annotation per cluster. Returns how many were posted."""
    posted = 0
    for rec in triage_records:
        c = rec["cluster"]
        verdict = rec.get("llm") or rec.get("rules")
        if not verdict:
            continue
        source = "llm" if rec.get("llm") else "rules"
        text = (
            f"[{source}] {c['company_id']} — {verdict['incident_type']} ({verdict['severity']})\n"
            f"{verdict['root_cause_hypothesis']}\n"
            f"Action: {verdict['recommended_action']}"
        )
        tags = ["ach", "anomaly", verdict["incident_type"], c["company_id"], source]
        annotate(
            settings,
            date.fromisoformat(c["start_date"]),
            date.fromisoformat(c["end_date"]),
            text,
            tags,
        )
        posted += 1
    return posted
