"""Authenticated, tenant-isolated persistence endpoints for development-only AI Lab.

The first release is strictly local: saves imports/plans/evidence, never calls
Gemini or Google; no remote device operation, subscription or public demo.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, member_api, parse_uuid
from ..models import (VisibilityExperiment, VisibilityGscSnapshot,
                      VisibilityObservation, VisibilityQuestion)
from ..services.audit import audit
from ..services.visibility_lab import LabInputError
from ..services import visibility_store as store

router = APIRouter(prefix="/api/v1/modules/visibility/storage", tags=["Visibility Lab storage"])


def _dev_only():
    if get_settings().is_production:
        raise HTTPException(404, "Unavailable in this environment.")


def _handle(action):
    try:
        return action()
    except LabInputError as exc:
        raise HTTPException(422, str(exc)) from None


class GscUpload(BaseModel):
    csv_text: str = Field(min_length=1, max_length=320_000)
    filename: str = Field(default="gsc-export.csv", min_length=1, max_length=200)
    date_start: str | None = Field(default=None, max_length=10)
    date_end: str | None = Field(default=None, max_length=10)


class QuestionReview(BaseModel):
    status: Literal["draft", "approved", "rejected"]


class QuestionSelection(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=300)


class PlanUpload(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    questions: list[QuestionSelection] = Field(min_length=1, max_length=200)
    markets: list[str] = Field(min_length=1, max_length=5)
    modes: list[str] = Field(min_length=1, max_length=3)
    repetitions: int = Field(default=1, ge=1, le=10)
    chunk_size: int = Field(default=5, ge=1, le=10)
    max_total: int = Field(default=100, ge=1, le=500)


class ImportedEvidence(BaseModel):
    source_run_id: str = Field(min_length=1, max_length=128)
    origin: Literal["historical_import", "canvas_runtime", "reconstructed", "synthetic"]
    question: str = Field(default="", max_length=500)
    actual_prompt: str = Field(default="", max_length=4000)
    answer: str = Field(default="", max_length=30000)
    provider: str = Field(default="", max_length=90)
    model_id: str = Field(default="", max_length=100)
    grounding: dict = Field(default_factory=dict)
    source_metadata: dict = Field(default_factory=dict)
    reported_mentioned: bool | None = None
    observed_at: str | None = Field(default=None, max_length=45)


class EvidenceBatch(BaseModel):
    runs: list[ImportedEvidence] = Field(min_length=1, max_length=100)
    experiment_id: str | None = Field(default=None, max_length=40)


@router.get("/snapshots")
def snapshots(db: TenantSession = Depends(get_db),
              p: Principal = Depends(member_api("read"))):
    _dev_only()
    rows = db.scalars(select(VisibilityGscSnapshot).order_by(
        VisibilityGscSnapshot.created_at.desc()).limit(100)).all()
    return {"snapshots": [store.snapshot_view(x) for x in rows], "source": "workspace_database"}


@router.post("/snapshots")
def create_snapshot(body: GscUpload, db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()
    def action():
        snap, created = store.save_gsc_snapshot(
            db, p.org.id, p.user.id, csv_text=body.csv_text,
            filename=body.filename, start=body.date_start, end=body.date_end)
        if created:
            audit(db, p.org.id, p.user.id, "visibility.gsc_import", "visibility_snapshot",
                  snap.id, {"rows": snap.row_count, "digest": snap.digest})
        return {"created": created, "snapshot": store.snapshot_view(snap)}
    return _handle(action)


@router.get("/snapshots/{snapshot_id}")
def snapshot_detail(snapshot_id: str, db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_api("read"))):
    _dev_only()
    snap = db.get(VisibilityGscSnapshot, parse_uuid(snapshot_id))
    if snap is None:
        raise HTTPException(404, "Snapshot not found.")
    return store.snapshot_view(snap, include_rows=True)


@router.post("/snapshots/{snapshot_id}/suggestions")
def create_suggestions(snapshot_id: str, db: TenantSession = Depends(get_db),
                       p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()
    def action():
        results = store.create_draft_questions(db, p.org.id, p.user.id, parse_uuid(snapshot_id))
        audit(db, p.org.id, p.user.id, "visibility.question_suggestions",
              "visibility_snapshot", snapshot_id,
              {"created": results["created"], "skipped": results["already_existed"]})
        return results
    return _handle(action)


@router.get("/questions")
def questions(db: TenantSession = Depends(get_db),
              p: Principal = Depends(member_api("read"))):
    _dev_only()
    rows = db.scalars(select(VisibilityQuestion).order_by(
        VisibilityQuestion.created_at.desc()).limit(250)).all()
    return {"questions": [store.question_view(q) for q in rows],
            "note": "First 250 most recent workspace questions."}


@router.post("/questions/{question_id}/review")
def review_question(question_id: str, body: QuestionReview,
                    db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()
    def action():
        question = store.set_question_review(db, p.org.id, parse_uuid(question_id), body.status)
        audit(db, p.org.id, p.user.id, "visibility.question_review", "visibility_question",
              question_id, {"status": body.status})
        return {"question": question}
    return _handle(action)


@router.get("/experiments")
def experiments(db: TenantSession = Depends(get_db),
                p: Principal = Depends(member_api("read"))):
    _dev_only()
    rows = db.scalars(select(VisibilityExperiment).order_by(
        VisibilityExperiment.created_at.desc()).limit(100)).all()
    return {"experiments": [store.experiment_view(x) for x in rows]}


@router.post("/experiments")
def create_experiment(body: PlanUpload, db: TenantSession = Depends(get_db),
                      p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()
    def action():
        ex = store.save_plan(
            db, p.org.id, p.user.id, title=body.title,
            questions=[q.model_dump() for q in body.questions],
            markets=body.markets, modes=body.modes,
            repeats=body.repetitions, chunk_size=body.chunk_size, max_total=body.max_total)
        audit(db, p.org.id, p.user.id, "visibility.plan_created", "visibility_experiment",
              ex["id"], {"request_count": ex["request_count"]})
        return {"experiment": ex, "execution": "not_scheduled", "billable_calls": 0}
    return _handle(action)


@router.get("/experiments/{experiment_id}")
def experiment_detail(experiment_id: str, db: TenantSession = Depends(get_db),
                      p: Principal = Depends(member_api("read"))):
    _dev_only()
    row = db.get(VisibilityExperiment, parse_uuid(experiment_id))
    if row is None:
        raise HTTPException(404, "Research plan not found.")
    return store.experiment_view(row, include_config=True)


@router.get("/evidence")
def evidence(db: TenantSession = Depends(get_db),
             p: Principal = Depends(member_api("read"))):
    _dev_only()
    rows = db.scalars(select(VisibilityObservation).order_by(
        VisibilityObservation.created_at.desc()).limit(100)).all()
    return {"counts": store.evidence_summary(db), "records": [
        store.evidence_view(x) for x in rows], "max_page": 100}


@router.get("/evidence/{evidence_id}")
def evidence_detail(evidence_id: str, db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_api("read"))):
    _dev_only()
    row = db.get(VisibilityObservation, parse_uuid(evidence_id))
    if row is None:
        raise HTTPException(404, "Evidence not found.")
    return store.evidence_view(row, with_text=True)


@router.post("/evidence/import")
def import_recorded_evidence(body: EvidenceBatch,
                             db: TenantSession = Depends(get_db),
                             p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()
    def action():
        experiment_id = parse_uuid(body.experiment_id) if body.experiment_id else None
        result = store.import_evidence(
            db, p.org.id, p.user.id, [r.model_dump() for r in body.runs],
            experiment_id=experiment_id)
        audit(db, p.org.id, p.user.id, "visibility.evidence_import",
              "visibility_observation", "",
              {"saved": result["saved"], "duplicates": result["duplicates"]})
        return result
    return _handle(action)
