"""Real disposable PostgreSQL tests for MF-019; never use a production database."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .conftest import csrf_of, onboard_business


def _make_org(name):
    from app.db import session_scope
    from app.models import Organization
    org_id = uuid.uuid4()
    with session_scope(org_id=org_id) as db:
        db.add(Organization(id=org_id, name=name))
    return org_id


def test_visibility_tables_have_force_rls_and_fail_closed(pg_database):
    from app.db import get_engine, isolation_self_check
    assert isolation_self_check() == []
    with get_engine().connect() as conn:
        names = ["visibility_gsc_snapshots", "visibility_questions",
                 "visibility_experiments", "visibility_observations"]
        results = conn.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(:names)"), {"names": names}).all()
        assert len(results) == 4
        assert all(row[1] and row[2] for row in results)
        for table in names:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_real_gsc_and_question_data_is_organization_private(pg_database):
    from app.db import session_scope
    from app.models import VisibilityGscSnapshot, VisibilityQuestion
    from app.services.visibility_store import save_gsc_snapshot, create_draft_questions

    a, b = _make_org("Private Studio A"), _make_org("Private Studio B")
    user = uuid.uuid4()
    with session_scope(org_id=a) as db:
        snap, created = save_gsc_snapshot(
            db, a, user, csv_text="Query,Clicks,Impressions,Position\n"
                                   "prescott general contractors,0,25,7.1\n",
            filename="a-test.csv")
        assert created is True
        source_id = snap.id
        suggestions = create_draft_questions(db, a, user, source_id)
        assert suggestions["created"] == 1
        q_id = uuid.UUID(suggestions["questions"][0]["id"])
    with session_scope(org_id=b) as db:
        assert db.get(VisibilityGscSnapshot, source_id) is None
        assert db.get(VisibilityQuestion, q_id) is None
        assert db.execute(text("SELECT count(*) FROM visibility_questions")).scalar() == 0
    with session_scope() as db:
        assert db.execute(text("SELECT count(*) FROM visibility_gsc_snapshots")).scalar() == 0
        assert db.execute(text("SELECT count(*) FROM visibility_questions")).scalar() == 0
    # Composite FK disallows a newly planted question claiming another org's snapshot.
    with pytest.raises(IntegrityError):
        with session_scope(org_id=b) as db:
            db.add(VisibilityQuestion(org_id=b, question="Impossible cross-tenant",
                                      question_hash="a"*64, source_snapshot_id=source_id,
                                      category="Residential remodeling", market="Prescott, Arizona",
                                      origin="gsc_suggestion", status="draft", source_queries=[]))
            db.flush()


def test_observations_immutable_to_update_and_tenant_export_includes_them(pg_database):
    from app.db import session_scope
    from app.models import VisibilityObservation
    from app.services.visibility_store import import_evidence
    from app.services.tenant_data import export_org

    a, b = _make_org("Evidence A"), _make_org("Evidence B")
    with session_scope(org_id=a) as db:
        result = import_evidence(db, a, uuid.uuid4(), [{
            "source_run_id": "run-live-xyz", "origin": "canvas_runtime",
            "question": "Who is a contractor in Prescott?",
            "answer": "External recorded observation", "reported_mentioned": False,
        }])
        assert result["records"][0]["provenance"] == "captured_unverified"
        row_id = uuid.UUID(result["records"][0]["id"])
    with session_scope(org_id=a) as db:
        modified = db.execute(text(
            "UPDATE visibility_observations SET answer='forged' WHERE id = :id"),
            {"id": row_id}).rowcount
        assert modified == 0, "Imported evidence must not be rewritten in place"
        bundle = export_org(db, a)
        assert len(bundle["tables"]["visibility_observations"]) == 1
        assert bundle["tables"]["visibility_observations"][0]["answer"] == "External recorded observation"
    with session_scope(org_id=b) as db:
        assert db.get(VisibilityObservation, row_id) is None


def test_visibility_storage_authenticated_api_enforces_csrf_and_no_execution(platform):
    owner, _, _ = onboard_business(
        platform, business="Visibility Test Shop", owner_email="visibility-owner@example.invalid")
    url = "/api/v1/modules/visibility/storage/snapshots"
    data = {"filename": "test.csv",
            "csv_text": "Query,Clicks,Impressions,Position\n"
                        "prescott kitchen remodeling,0,3,17.2"}
    unauthorized = owner.post(url, json=data)
    assert unauthorized.status_code == 403, unauthorized.text[:200]
    authorized = owner.post(url, json=data,
                            headers={"X-CSRF-Token": csrf_of(owner)})
    assert authorized.status_code == 200, authorized.text[:300]
    payload = authorized.json()
    assert payload["created"] is True
    assert payload["snapshot"]["row_count"] == 1
    assert owner.get(url).json()["snapshots"][0]["filename"] == "test.csv"
    assert owner.get("/api/v1/modules/visibility/storage/experiments").json()["experiments"] == []
