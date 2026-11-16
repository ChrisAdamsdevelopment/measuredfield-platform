"""MF-025 first-party lead rescue algorithm and real tenant isolation tests."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.lead_rescue import classify, summarize

NOW = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)


def fake_lead(name, *, status="new", age_hours=48,
              follow_up_on=None, recorded=False, assigned=False):
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, status=status, service="Kitchen remodel",
        source="website", assigned_to=uuid.uuid4() if assigned else None,
        created_at=NOW - timedelta(hours=age_hours),
        first_response_at=NOW - timedelta(hours=1) if recorded else None,
        follow_up_on=follow_up_on,
    )


def test_action_buckets_preserve_response_and_followup_evidence():
    today = NOW.date()
    leads = [
        fake_lead("Overdue", follow_up_on=today - timedelta(days=2), age_hours=96),
        fake_lead("Due", follow_up_on=today, age_hours=36),
        fake_lead("Unanswered", follow_up_on=today + timedelta(days=1), age_hours=25),
        fake_lead("Unscheduled", follow_up_on=None, age_hours=3),
        fake_lead("Watching", follow_up_on=today + timedelta(days=3),
                  age_hours=12, recorded=True, assigned=True),
        fake_lead("Already won", status="won", follow_up_on=today - timedelta(days=9)),
    ]
    report = summarize(leads, as_of=NOW, total_open=5)
    assert report["counts"] == {
        "overdue": 1, "due_today": 1, "unanswered_24h": 1,
        "needs_schedule": 1, "watching": 1,
    }
    assert report["sampled_open"] == 5
    assert report["coverage_complete"] is True
    assert [x["name"] for x in report["items"]] == [
        "Overdue", "Due", "Unanswered", "Unscheduled", "Watching",
    ]
    assert report["items"][4]["response_recorded"] is True
    assert report["items"][3]["assigned"] is False
    assert "not independent proof" in report["note"]
    assert "revenue" in report["note"]
    assert classify(leads[-1], as_of=NOW) is None


def test_unknown_timestamps_never_fabricate_response_aging():
    lead = fake_lead("Unknown date", age_hours=25)
    lead.created_at = None
    result = classify(lead, as_of=NOW)
    assert result["age_hours"] is None
    assert result["priority"] == "needs_schedule"
    future = fake_lead("Future bad timestamp", age_hours=-12)
    assert classify(future, as_of=NOW)["age_hours"] is None
    report = summarize([lead, future], as_of=NOW, total_open=200)
    assert report["coverage_complete"] is False
    assert report["sampled_open"] == 2
    assert report["total_open"] == 200


def test_lead_rescue_is_only_authenticated_get_and_production_gated():
    from app.routers.lead_rescue import router
    routes = [r for r in router.routes if r.path == "/app/lead-rescue"]
    assert len(routes) == 1
    assert routes[0].methods == {"GET"}


def test_real_tenant_lead_rescue_never_leaks_other_business(platform):
    from app.db import session_scope
    from app.models import Lead
    from .conftest import client_for, onboard_business

    owner_a, org_a, _ = onboard_business(
        platform, business="Alder Lead Rescue",
        owner_email="alder-rescue-owner@example.invalid")
    owner_b, org_b, _ = onboard_business(
        platform, business="Birch Lead Rescue",
        owner_email="birch-rescue-owner@example.invalid")

    with session_scope(org_id=org_a) as db:
        db.add(Lead(
            org_id=org_a, name="ALDER-RESCUE-SECRET-43", email="a@example.invalid",
            message="Synthetic overdue inquiry", service="Trim installation",
            status="new", follow_up_on=date.today() - timedelta(days=2),
            created_at=datetime.now(timezone.utc) - timedelta(days=4),
        ))
    with session_scope(org_id=org_b) as db:
        db.add(Lead(
            org_id=org_b, name="BIRCH-RESCUE-SECRET-77", email="b@example.invalid",
            message="Synthetic owner-only inquiry", service="Kitchen remodeling",
            status="new", follow_up_on=date.today() - timedelta(days=1),
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
        ))

    response_a = owner_a.get("/app/lead-rescue")
    response_b = owner_b.get("/app/lead-rescue")
    assert response_a.status_code == 200, response_a.text[:400]
    assert response_b.status_code == 200, response_b.text[:400]
    assert "ALDER-RESCUE-SECRET-43" in response_a.text
    assert "BIRCH-RESCUE-SECRET-77" not in response_a.text
    assert "BIRCH-RESCUE-SECRET-77" in response_b.text
    assert "ALDER-RESCUE-SECRET-43" not in response_b.text
    assert "0 fake" not in response_a.text
    assert "No guessed sales value" in response_a.text
    anon = client_for(platform)
    assert anon.get("/app/lead-rescue").status_code in (302, 303, 401)
