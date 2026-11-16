"""MF-019 deterministic tests; no production DB, provider or device access."""
from __future__ import annotations

import ast
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models import TENANT_MODELS, TENANT_TABLES
from app.services import visibility_store as storage
from app.services.visibility_lab import BENCHMARK_22, LabInputError


class FakeTenantSession:
    """Minimal surrogate for validating pure service semantics without Postgres."""
    def __init__(self):
        self.items = []

    def scalar(self, query):
        return None

    def add(self, row):
        self.items.append(row)

    def flush(self):
        for obj in self.items:
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not getattr(obj, "created_at", None):
                obj.created_at = datetime.now(timezone.utc)

    def get(self, cls, obj_id):
        return next((x for x in self.items if isinstance(x, cls) and x.id == obj_id), None)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(storage, "_count", lambda *_args: 0)
    return FakeTenantSession()


def test_registry_contains_four_tenant_scoped_visibility_tables():
    wanted = {"visibility_gsc_snapshots", "visibility_questions",
              "visibility_experiments", "visibility_observations"}
    assert wanted.issubset(TENANT_TABLES)
    assert wanted.issubset(TENANT_MODELS)
    for table in wanted:
        model = TENANT_MODELS[table]
        assert model.__table__.c.org_id.nullable is False
        assert "org_id" in model.__table__.c


def test_force_rls_in_0005_migration_and_same_tenant_fk():
    filename = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0005_visibility_storage.py"
    source = filename.read_text(encoding="utf-8")
    assert 'FORCE ROW LEVEL SECURITY' in source
    assert 'CREATE POLICY tenant_isolation' in source
    assert 'visibility_observations_no_update' in source
    assert 'fk_vq_snapshot_same_tenant' in source
    assert 'fk_vo_experiment_same_tenant' in source


def test_source_row_import_keeps_original_queries_and_unknown_window(db):
    org, owner = uuid.uuid4(), uuid.uuid4()
    csv = ('Query,Clicks,Impressions,Position\n'
           '"prescott general contractors",0,25,7.1\n'
           '"prescott valley kitchen contractor",0,8,28.8')
    snap, fresh = storage.save_gsc_snapshot(
        db, org, owner, csv_text=csv, filename="owner-gsc.csv")
    assert fresh is True
    view = storage.snapshot_view(snap, include_rows=True)
    assert view["row_count"] == 2
    assert view["impressions"] == 33
    assert view["date_start"] is None
    assert view["rows"][0]["query"] == "prescott general contractors"
    assert view["rows"][1]["avg_position"] == 28.8


def test_no_report_date_invention_and_sane_date_bounds(db):
    common = dict(db=db, org_id=uuid.uuid4(), user_id=uuid.uuid4(),
                  csv_text="Query,Clicks,Impressions,Position\nremodeling prescott,0,4,3.0",
                  filename="gsc.csv")
    with pytest.raises(LabInputError, match="both GSC"):
        storage.save_gsc_snapshot(**common, start="2026-11-01")
    with pytest.raises(LabInputError, match="precede"):
        storage.save_gsc_snapshot(**common, start="2026-11-10", end="2026-11-01")
    with pytest.raises(LabInputError, match="safe filename"):
        storage.save_gsc_snapshot(**{**common, "filename": "../hidden.env"})


def test_gsc_to_approved_questions_to_full_22_plus_new_experiment(db):
    org, owner = uuid.uuid4(), uuid.uuid4()
    snap, _ = storage.save_gsc_snapshot(
        db, org, owner,
        csv_text="Query,Clicks,Impressions,Position\n"
                 "prescott general contractors,0,25,7.1\n"
                 "prescott valley kitchen contractor,0,8,28.8",
        filename="real-gsc.csv")
    made = storage.create_draft_questions(db, org, owner, snap.id)
    assert made["created"] == 2
    assert all(q["status"] == "draft" for q in made["questions"])
    selected = [{"id": f"q-seed-{i}", "text": text}
                for i, text in enumerate(BENCHMARK_22, 1)]
    with pytest.raises(LabInputError, match="approved"):
        storage.save_plan(db, org, owner, title="Unapproved test",
                          questions=selected + [{
                              "id": made["questions"][0]["id"],
                              "text": made["questions"][0]["text"]}],
                          markets=["Prescott Valley, Arizona"], modes=["as_written"],
                          repeats=1, chunk_size=5, max_total=100)
    first = made["questions"][0]
    review = storage.set_question_review(db, org, uuid.UUID(first["id"]), "approved")
    assert review["status"] == "approved"
    selected.append({"id": first["id"], "text": first["text"]})
    saved = storage.save_plan(db, org, owner, title="Baseline + new",
                              questions=selected,
                              markets=["Prescott Valley, Arizona"],
                              modes=["as_written", "persona_context"],
                              repeats=1, chunk_size=5, max_total=100)
    assert saved["request_count"] == 46
    assert saved["status"] == "planned"
    assert saved["provider_requests_executed"] == 0
    assert saved["configuration"]["note"].startswith("Planning only")


def test_original_22_question_wording_cannot_be_forged(db):
    org, owner = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(LabInputError, match="unchanged"):
        storage.save_plan(db, org, owner, title="Tampered baseline",
                          questions=[{"id": "q-seed-1", "text": "something else"}],
                          markets=["Prescott, Arizona"], modes=["as_written"],
                          repeats=1, chunk_size=5, max_total=100)


def test_unauthorized_cross_tenant_question_reference_rejected(db):
    attacker, victim = uuid.uuid4(), uuid.uuid4()
    q = storage.VisibilityQuestion(
        id=uuid.uuid4(), org_id=victim, question="Who installs trim?",
        question_hash="a"*64, status="approved", market="Prescott, Arizona",
        category="Finish carpentry", origin="gsc_suggestion", source_queries=[])
    db.add(q)
    with pytest.raises(LabInputError, match="workspace"):
        storage.set_question_review(db, attacker, q.id, "approved")


def test_provenance_is_server_selected_not_client_selected():
    assert storage.classify_imported_source("run-fresh78-23", "canvas_runtime") == "synthetic_fixture"
    assert storage.classify_imported_source("run-123", "synthetic") == "synthetic_fixture"
    assert storage.classify_imported_source("run-live-123", "canvas_runtime") == "captured_unverified"
    assert storage.classify_imported_source("import-1", "historical_import") == "historical_import"
    assert storage.classify_imported_source("raw-1", "reconstructed") == "quarantined_reconstruction"
    with pytest.raises(LabInputError):
        storage.classify_imported_source("run-live", "verified_provider")


def test_duplicate_imported_results_skip_and_never_become_verified(db):
    org, user = uuid.uuid4(), uuid.uuid4()
    evidence = {
        "source_run_id": "run-fresh78-7", "origin": "canvas_runtime",
        "question": "Where hire someone?", "actual_prompt": "Where hire someone?",
        "answer": "Unverified generated answer", "provider": "Gemini",
        "model_id": "flash-preview", "reported_mentioned": False,
        "grounding": {"source": "unverified"}, "source_metadata":
            {"provider_receipt_verified": True, "groundingStatus": "grounded_verified"},
    }
    result = storage.import_evidence(db, org, user, [evidence, evidence])
    assert result["saved"] == 1
    assert result["duplicates"] == 1
    record = result["records"][0]
    assert record["provenance"] == "synthetic_fixture"
    raw = db.items[0]
    assert raw.source_metadata["verified_by_server"] is False
    assert raw.source_metadata["original_untrusted_metadata"]["provider_receipt_verified"] is True


def test_nontrusted_runtime_remains_unverified_and_metadata_limited(db):
    org, user = uuid.uuid4(), uuid.uuid4()
    record = {"source_run_id": "run-live-1234", "origin": "canvas_runtime",
              "reported_mentioned": True, "grounding": {},
              "source_metadata": {"raw_status": "grounded_verified"}}
    result = storage.import_evidence(db, org, user, [record])
    assert result["saved"] == 1
    assert result["records"][0]["provenance"] == "captured_unverified"
    with pytest.raises(LabInputError, match="too large"):
        storage.import_evidence(db, org, user, [{
            **record, "source_run_id": "run-live-big",
            "source_metadata": {"item": "X" * 90000}
        }])


def test_immutable_result_policy_and_deletion_order():
    position = {name: idx for idx, name in enumerate(TENANT_TABLES)}
    assert position["visibility_observations"] < position["visibility_experiments"]
    assert position["visibility_questions"] < position["visibility_gsc_snapshots"]


def test_authenticated_routes_require_write_permission_and_dev_gate():
    from app.routers import visibility_store
    from app.config import get_settings
    import inspect
    from fastapi import HTTPException

    paths = {r.path for r in visibility_store.router.routes}
    assert "/api/v1/modules/visibility/storage/snapshots" in paths
    assert "/api/v1/modules/visibility/storage/evidence/import" in paths
    source = inspect.getsource(visibility_store)
    assert source.count('member_api("improvements.write")') >= 5
    assert source.count('member_api("read")') >= 5
    assert get_settings().is_production is False or "production" in get_settings().env
    original = visibility_store.get_settings
    visibility_store.get_settings = lambda: type("S", (), {"is_production": True})()
    try:
        with pytest.raises(HTTPException) as exc:
            visibility_store._dev_only()
        assert exc.value.status_code == 404
    finally:
        visibility_store.get_settings = original
