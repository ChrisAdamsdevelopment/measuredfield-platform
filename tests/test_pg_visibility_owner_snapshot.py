"""Real PostgreSQL isolation and provenance integrity for private AI Lab evidence."""
from __future__ import annotations

import uuid

from app.db import session_scope
from app.models import VisibilityObservation, now
from app.services.visibility_owner_snapshot import owner_evidence_health
from .conftest import client_for, onboard_business


def _run(db, org_id, run_id, provenance, mentioned, *, attested=False):
    db.add(VisibilityObservation(
        org_id=org_id, source_run_id=run_id,
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
        _run(db, b_id, "actually-server-verified-b", "verified_provider", True, attested=True)
        _run(db, b_id, "actual-server-no-b", "verified_provider", False, attested=True)

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
        _run(db, b, "direct-verified-b", "verified_provider", True, attested=True)
    with session_scope(org_id=a) as db:
        assert owner_evidence_health(db, a)["record_count"] == 1
        assert owner_evidence_health(db, a)["verified_mention_percent"] is None
    with session_scope(org_id=b) as db:
        assert owner_evidence_health(db, b)["record_count"] == 1
        assert owner_evidence_health(db, b)["verified_mention_percent"] == 100.0
    with session_scope() as db:
        assert owner_evidence_health(db, a)["record_count"] == 0
