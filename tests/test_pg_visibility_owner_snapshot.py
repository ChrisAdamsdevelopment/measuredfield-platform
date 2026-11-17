"""Real PostgreSQL isolation and provenance integrity for private AI Lab evidence."""
from __future__ import annotations

import uuid

from app.db import session_scope
from app.models import VisibilityObservation, now
from app.services.visibility_owner_snapshot import owner_evidence_health
from .conftest import client_for, onboard_business


def _plan(db, org_id, title="Synthetic research round"):
    from app.models import VisibilityExperiment
    plan = VisibilityExperiment(org_id=org_id, title=title, configuration={}, request_count=1)
    db.add(plan)
    db.flush()
    return plan.id


def _run(db, org_id, run_id, provenance, mentioned, *, attested=False, experiment_id=None):
    db.add(VisibilityObservation(
        org_id=org_id, source_run_id=run_id, experiment_id=experiment_id,
        provenance=provenance,
        question="Who should help with remodeling in the area?",
        actual_prompt="Who should help with remodeling in the area?",
        answer="The assistant mentioned a remodeling company.",
        provider="synthetic_test_provider", model_id="synthetic-test-model",
        grounding={},
        source_metadata=(
            {"verified_by_server": True, "provider_receipt_id": "synthetic-receipt-" + run_id}
            if attested else {"verified_by_server": False,
                              "provider_receipt_id": "invented-" + run_id}
        ),
        reported_mentioned=mentioned,
        observed_at=now(),
    ))


def test_authenticated_owner_research_page_is_provenance_safe_and_tenant_isolated(platform):
    alice, a_id, _ = onboard_business(
        platform, business="Evidence Lab Alpha", owner_email="lab-evidence-alpha@fictional.example")
    bob, b_id, _ = onboard_business(
        platform, business="Evidence Lab Beta", owner_email="lab-evidence-beta@fictional.example")
    with session_scope(org_id=a_id) as db:
        _run(db, a_id, "fake-verified-flag", "verified_provider", True, attested=False)
        _run(db, a_id, "captured-live-a", "captured_unverified", True)
        _run(db, a_id, "historical-a", "historical_import", False)
        _run(db, a_id, "run-fresh78-1", "synthetic_fixture", True)
        _run(db, a_id, "unknown-label-a", "provider_proven", True)
    with session_scope(org_id=b_id) as db:
        plan = _plan(db, b_id)
        _run(db, b_id, "actually-server-verified-b", "verified_provider", True, attested=True, experiment_id=plan)
        _run(db, b_id, "actual-server-no-b", "verified_provider", False, attested=True, experiment_id=plan)

    a_api = alice.get("/api/v1/modules/visibility/evidence-health")
    assert a_api.status_code == 200, a_api.text[:250]
    summary = a_api.json()["summary"]
    assert summary["record_count"] == 5
    assert summary["verified_receipts"] == 0
    assert summary["verified_mention_percent"] is None
    assert summary["excluded_or_unverified"] == 5
    assert summary["status"] == "awaiting_provider_receipts"
    by_key = {x["key"]: x for x in summary["buckets"]}
    assert by_key["needs_review"]["records"] == 2
    assert by_key["captured_unverified"]["records"] == 1
    assert by_key["historical_import"]["records"] == 1
    assert by_key["synthetic_fixture"]["records"] == 1
    assert "not independently verified" in summary["headline"]

    a_page = alice.get("/app/modules/visibility/evidence-health")
    assert a_page.status_code == 200, a_page.text[:220]
    assert "No trustworthy recommendation percentage to show yet" in a_page.text
    assert "Practice examples" in a_page.text
    assert "5" in a_page.text
    assert "synthetic-receipt-actually-server-verified-b" not in a_page.text

    b_summary = bob.get("/api/v1/modules/visibility/evidence-health").json()["summary"]
    assert b_summary["record_count"] == 2
    assert b_summary["verified_receipts"] == 2
    assert b_summary["verified_answered"] == 2
    assert b_summary["verified_mentioned"] == 1
    assert b_summary["verified_mention_percent"] == 50.0
    b_page = bob.get("/app/modules/visibility/evidence-health")
    assert "1 out of 2 checked AI answers" in b_page.text


def test_evidence_health_requires_sign_in_and_no_mutations(platform):
    anonymous = client_for(platform)
    assert anonymous.get("/api/v1/modules/visibility/evidence-health").status_code == 401
    assert anonymous.get("/app/modules/visibility/evidence-health").status_code in (302, 303)
    owner, _, _ = onboard_business(
        platform, business="Evidence Auth", owner_email="evidence-auth@fictional.example")
    assert owner.post("/api/v1/modules/visibility/evidence-health", json={}).status_code == 405


def test_direct_db_summary_keeps_tenant_boundary_even_with_unknown_labels(pg_database):
    from app.models import Organization

    a, b = uuid.uuid4(), uuid.uuid4()
    with session_scope(org_id=a) as db:
        db.add(Organization(id=a, name="Direct Evidence A"))
        db.flush()
        _run(db, a, "direct-captured-a", "captured_unverified", True)
    with session_scope(org_id=b) as db:
        db.add(Organization(id=b, name="Direct Evidence B"))
        db.flush()
        _run(db, b, "direct-verified-b", "verified_provider", True, attested=True, experiment_id=_plan(db, b))
    with session_scope(org_id=a) as db:
        assert owner_evidence_health(db, a)["record_count"] == 1
        assert owner_evidence_health(db, a)["verified_mention_percent"] is None
    with session_scope(org_id=b) as db:
        assert owner_evidence_health(db, b)["record_count"] == 1
        assert owner_evidence_health(db, b)["verified_mention_percent"] == 100.0
    with session_scope() as db:
        assert owner_evidence_health(db, a)["record_count"] == 0


# ------------------------------------------------------------- review round 2 (Claude)

def _attested(db, org_id, run_id, mentioned, meta, *, experiment_id, model="synthetic-test-model", when=None):
    db.add(VisibilityObservation(
        org_id=org_id, source_run_id=run_id, experiment_id=experiment_id, provenance="verified_provider",
        question="Who should help with remodeling in the area?", actual_prompt="Who should help?",
        answer="An answer.", provider="synthetic_test_provider", model_id=model, grounding={},
        source_metadata=meta, reported_mentioned=mentioned, observed_at=when or now()))


def test_verified_check_respects_json_types_not_just_text(pg_database):
    """->> text extraction let the STRING 'true' and non-string receipts through."""
    from app.models import Organization
    org = uuid.uuid4()
    good = {"verified_by_server": True, "provider_receipt_id": "rcpt_valid_0001"}
    bad = {
        "flag_string": {"verified_by_server": "true", "provider_receipt_id": "rcpt_valid_0001"},
        "receipt_number": {"verified_by_server": True, "provider_receipt_id": 1234567890},
        "receipt_list": {"verified_by_server": True, "provider_receipt_id": ["a", "b", "c", "d"]},
        "receipt_object": {"verified_by_server": True, "provider_receipt_id": {"id": "rcpt_valid_0001"}},
        "receipt_spaces": {"verified_by_server": True, "provider_receipt_id": "        "},
        "receipt_short": {"verified_by_server": True, "provider_receipt_id": "abc"},
        "receipt_odd_chars": {"verified_by_server": True, "provider_receipt_id": "rcpt valid <x>"},
        "flag_null": {"verified_by_server": None, "provider_receipt_id": "rcpt_valid_0001"},
        "nested_only": {"original_untrusted_metadata": good},
    }
    with session_scope(org_id=org) as db:
        db.add(Organization(id=org, name="Typed Evidence Co"))
        db.flush()
        plan = _plan(db, org)
        _attested(db, org, "good-1", True, good, experiment_id=plan)
        for name, meta in bad.items():
            _attested(db, org, "bad-" + name, True, meta, experiment_id=plan)
        summary = owner_evidence_health(db, org)
    review = {b["key"]: b for b in summary["buckets"]}["needs_review"]
    assert summary["verified_receipts"] == 1 and review["records"] == len(bad)
    assert summary["verified_mention_percent"] == 100.0


def test_rounds_are_shown_separately_and_never_pooled(platform):
    """The 27.3% vs 47.1% trap: two different rounds must not become one 35.9% headline."""
    from datetime import datetime, timezone
    owner, org, _ = onboard_business(platform, business="Round Split Co", owner_email="owner@round-split.example")
    receipt = {"verified_by_server": True, "provider_receipt_id": "rcpt_round_split"}
    with session_scope(org_id=org) as db:
        old, new = _plan(db, org, "June round"), _plan(db, org, "November round")
        for i in range(22):
            _attested(db, org, f"old-{i}", i < 6, receipt, experiment_id=old, model="model-2025",
                      when=datetime(2026, 6, 1, tzinfo=timezone.utc))
        for i in range(17):
            _attested(db, org, f"new-{i}", i < 8, receipt, experiment_id=new, model="model-2026",
                      when=datetime(2026, 11, 15, tzinfo=timezone.utc))
    summary = owner.get("/api/v1/modules/visibility/evidence-health").json()["summary"]
    assert summary["verified_mention_percent"] is None and summary["status"] == "multiple_rounds"
    assert sorted(c["mention_percent"] for c in summary["cohorts"]) == [27.3, 47.1]
    page = owner.get("/app/modules/visibility/evidence-health").text
    assert "Results by research round" in page and "6 of 22 (27.3%)" in page and "8 of 17 (47.1%)" in page
    assert "35.9" not in page and "not proof that something improved" in page


def test_unplanned_verified_answers_get_no_percentage(platform):
    owner, org, _ = onboard_business(platform, business="No Plan Co", owner_email="owner@no-plan.example")
    receipt = {"verified_by_server": True, "provider_receipt_id": "rcpt_no_plan_01"}
    with session_scope(org_id=org) as db:
        _attested(db, org, "np-1", True, receipt, experiment_id=None, model="m1")
        _attested(db, org, "np-2", False, receipt, experiment_id=None, model="m2")
    summary = owner.get("/api/v1/modules/visibility/evidence-health").json()["summary"]
    assert summary["verified_receipts"] == 2 and summary["verified_mention_percent"] is None
    assert summary["status"] == "verified_without_plan"
    page = owner.get("/app/modules/visibility/evidence-health").text
    assert "not collected as part of one research plan" in page and "50.0" not in page
