"""Privacy-minimal, first-party lead response metrics for the owner Insights page.

Never guesses traffic, revenue or actual customer communications.
first_response_at is a *workflow record*, not independent proof of a sent message.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable


def _aware(value: datetime) -> datetime:
    # Database timestamps are timezone-aware; the fallback supports older test fixtures.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def summarize_responses(
    leads: Iterable,
    *,
    as_of: datetime | None = None,
    window_days: int = 30,
    target_hours: int = 24,
) -> dict:
    """Summarize only actual recorded lead timestamps over a bounded window.

    The target is an internal tracking threshold, NOT a promise made to customers.
    Percent on-time uses all leads old enough to have reached the target window,
    avoiding inflated success from newly received, not-yet-due inquiries.
    """
    if not 1 <= window_days <= 366 or not 1 <= target_hours <= 168:
        raise ValueError("Invalid measurement window or internal response target")
    end = _aware(as_of or datetime.now(timezone.utc))
    cutoff = end - timedelta(days=window_days)
    due_cutoff = end - timedelta(hours=target_hours)
    response_hours: list[float] = []
    total = responded = due = on_time = overdue = anomalies = 0
    for lead in leads:
        created_raw = getattr(lead, "created_at", None)
        if not isinstance(created_raw, datetime):
            anomalies += 1
            continue
        created = _aware(created_raw)
        if created < cutoff or created > end:
            anomalies += int(created > end)
            continue
        total += 1
        response_raw = getattr(lead, "first_response_at", None)
        response = _aware(response_raw) if isinstance(response_raw, datetime) else None
        if response and (response < created or response > end):
            anomalies += 1
            response = None
        elapsed = (response - created).total_seconds() / 3600 if response else None
        if elapsed is not None:
            responded += 1
            response_hours.append(elapsed)
        if created <= due_cutoff:
            due += 1
            if elapsed is not None and elapsed <= target_hours:
                on_time += 1
            if response is None and getattr(lead, "status", "new") not in ("lost", "won", "spam"):
                overdue += 1
    ordered = sorted(response_hours)
    percentile90 = ordered[max(0, (9 * len(ordered) + 9) // 10 - 1)] if ordered else None
    return {
        "window_days": window_days,
        "target_hours": target_hours,
        "leads_total": total,
        "responses_recorded": responded,
        "unanswered": total - responded,
        "eligible_for_target": due,
        "on_time": on_time,
        "on_time_pct": round(on_time / due * 100, 1) if due else None,
        "median_hours": round(median(ordered), 2) if ordered else None,
        "p90_hours": round(percentile90, 2) if percentile90 is not None else None,
        "overdue_open": overdue,
        "timestamp_anomalies": anomalies,
        "provenance": "measured_first_party_workflow",
        "caveat": "Status changes record a first response; they do not verify an email, call or text was sent.",
    }
