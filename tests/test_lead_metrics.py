"""Owner-facing response metrics must use recorded evidence and honest samples."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.lead_metrics import summarize_responses

NOW = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)


def lead(*, age_hours=30, respond_after_hours=None, status="new"):
    created = NOW - timedelta(hours=age_hours)
    response = (created + timedelta(hours=respond_after_hours)
                if respond_after_hours is not None else None)
    return SimpleNamespace(created_at=created, first_response_at=response, status=status)


def test_empty_observations_are_unknown_not_zero_time():
    result = summarize_responses([], as_of=NOW)
    assert result["leads_total"] == 0
    assert result["median_hours"] is None
    assert result["on_time_pct"] is None
    assert result["provenance"] == "measured_first_party_workflow"


def test_recorded_response_median_and_90th_percentile():
    result = summarize_responses([
        lead(age_hours=100, respond_after_hours=1),
        lead(age_hours=60, respond_after_hours=3),
        lead(age_hours=48, respond_after_hours=10),
    ], as_of=NOW)
    assert result["responses_recorded"] == 3
    assert result["median_hours"] == 3
    assert result["p90_hours"] == 10
    assert result["on_time_pct"] == 100.0


def test_due_denominator_excludes_recent_not_yet_eligible_inquiries():
    result = summarize_responses([
        lead(age_hours=72, respond_after_hours=2),
        lead(age_hours=26),
        lead(age_hours=2),
    ], as_of=NOW)
    assert result["leads_total"] == 3
    assert result["eligible_for_target"] == 2
    assert result["on_time"] == 1
    assert result["on_time_pct"] == 50.0
    assert result["overdue_open"] == 1


def test_closed_spam_is_not_shown_as_open_overdue():
    result = summarize_responses([lead(age_hours=72, status="spam")], as_of=NOW)
    assert result["eligible_for_target"] == 1
    assert result["overdue_open"] == 0


def test_older_data_is_outside_window_and_future_dates_are_invalid():
    result = summarize_responses([
        lead(age_hours=24 * 32),
        lead(age_hours=-5),
        lead(age_hours=25, respond_after_hours=40),
    ], as_of=NOW)
    assert result["leads_total"] == 1
    assert result["responses_recorded"] == 0
    assert result["timestamp_anomalies"] == 2


def test_invalid_thresholds_are_rejected():
    with pytest.raises(ValueError):
        summarize_responses([], as_of=NOW, target_hours=0)
    with pytest.raises(ValueError):
        summarize_responses([], as_of=NOW, window_days=1000)


def test_insights_template_references_actual_provenance_not_revenue_assumptions():
    from app.deps import templates
    text = templates.env.get_template("app/insights.html").render(
        brand="MeasuredField",
        settings=SimpleNamespace(env="test", is_production=False),
        principal=SimpleNamespace(user=SimpleNamespace(
            name="Synthetic Owner", email="owner@example.invalid", is_platform_admin=False)),
        org=SimpleNamespace(name="Demo", status="active"),
        orgs=[], role="owner", can=lambda _: True, csrf="fake-csrf",
        path="/app/insights",
        measured={"page_view": 9, "form_start": 2, "lead": 1},
        leads_responded=1, leads_total=2,
        response_metrics=summarize_responses(
            [lead(age_hours=48, respond_after_hours=2), lead(age_hours=36)], as_of=NOW),
        report=None, node=False,
    )
    assert "Median recorded first-response time" in text
    assert "internal tracking target" in text
    assert "unknown when no qualifying records exist" in text
