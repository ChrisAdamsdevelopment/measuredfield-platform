"""MF-025: deterministic, provenance-honest inquiry rescue prioritization.

Works on first-party Lead records already owned by a tenant. This module never
communicates with customers or estimates revenue. The date comparisons are
explicit to make outcomes repeatable in tests and human review.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

OPEN_STATUSES = frozenset({"new", "contacted", "qualified"})
PRIORITIES = ("overdue", "due_today", "unanswered_24h", "needs_schedule", "watching")
LABELS = {
    "overdue": "Overdue scheduled follow-up",
    "due_today": "Follow-up due today",
    "unanswered_24h": "New inquiry without recorded response after 24 hours",
    "needs_schedule": "Open inquiry with no follow-up date",
    "watching": "Open inquiry scheduled for later",
}
NEXT_ACTIONS = {
    "overdue": "Review the inquiry and complete or reschedule the overdue follow-up.",
    "due_today": "Review the inquiry and perform the planned follow-up today.",
    "unanswered_24h": "Check whether the customer has been contacted and record the result.",
    "needs_schedule": "Choose a follow-up date or close out this inquiry.",
    "watching": "Confirm assignment, next steps and the future follow-up date.",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def classify(lead, *, as_of: datetime) -> dict | None:
    """Return one primary action category, without inventing response evidence."""
    status = getattr(lead, "status", "")
    if status not in OPEN_STATUSES:
        return None
    now = _aware(as_of)
    today = now.date()
    when = getattr(lead, "follow_up_on", None)
    if not isinstance(when, date):
        when = None
    created_raw = getattr(lead, "created_at", None)
    age_hours = None
    if isinstance(created_raw, datetime):
        delta = (now - _aware(created_raw)).total_seconds()
        if delta >= 0:
            age_hours = round(delta / 3600, 1)
    recorded_response = getattr(lead, "first_response_at", None)
    response_recorded = isinstance(recorded_response, datetime)

    if when is not None and when < today:
        priority = "overdue"
    elif when is not None and when == today:
        priority = "due_today"
    elif status == "new" and not response_recorded and age_hours is not None and age_hours >= 24:
        priority = "unanswered_24h"
    elif when is None:
        priority = "needs_schedule"
    else:
        priority = "watching"

    return {
        "id": str(lead.id),
        "name": str(getattr(lead, "name", ""))[:100],
        "service": str(getattr(lead, "service", ""))[:100],
        "status": status,
        "priority": priority,
        "priority_label": LABELS[priority],
        "next_action": NEXT_ACTIONS[priority],
        "follow_up_on": when.isoformat() if when else None,
        "age_hours": age_hours,
        "response_recorded": response_recorded,
        "assigned": getattr(lead, "assigned_to", None) is not None,
        "source": str(getattr(lead, "source", "unknown"))[:40],
    }


def summarize(leads: Iterable, *, as_of: datetime,
              total_open: int | None = None, maximum: int = 500) -> dict:
    if not 1 <= maximum <= 5000:
        raise ValueError("Maximum sampled leads must be between 1 and 5000")
    seen = 0
    items = []
    counts = {key: 0 for key in PRIORITIES}
    for lead in leads:
        if seen >= maximum:
            break
        seen += 1
        item = classify(lead, as_of=as_of)
        if item is None:
            continue
        counts[item["priority"]] += 1
        items.append(item)
    items.sort(key=lambda item: (
        PRIORITIES.index(item["priority"]),
        -(item["age_hours"] if item["age_hours"] is not None else -1),
        item["id"],
    ))
    total_open = int(total_open) if total_open is not None else len(items)
    return {
        "as_of": _aware(as_of).isoformat(),
        "counts": counts,
        "sampled_open": len(items),
        "total_open": total_open,
        "coverage_complete": total_open <= len(items),
        "maximum_reviewed": maximum,
        "items": items,
        "target_hours": 24,
        "provenance": "first_party_workflow_records",
        "note": (
            "Lead status and first_response_at are internal workflow records, "
            "not independent proof of email, call, SMS delivery or booking. "
            "The tool does not predict revenue or conversion."
        ),
    }


def priority_case(today: date, response_cutoff: datetime):
    """SQL sort matching the classification precedence; urgent leads first."""
    from sqlalchemy import and_, case
    from ..models import Lead

    return case(
        (Lead.follow_up_on < today, 0),
        (Lead.follow_up_on == today, 1),
        (and_(Lead.status == "new", Lead.first_response_at.is_(None),
              Lead.created_at <= response_cutoff), 2),
        (Lead.follow_up_on.is_(None), 3),
        else_=4,
    )
