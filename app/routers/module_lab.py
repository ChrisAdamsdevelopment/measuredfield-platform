"""Private, development-only MeasuredField module suite. No paid or device actions."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, member, member_api, render
from ..models import VisibilityObservation
from ..module_catalog import catalog, connection_policy
from ..services.audit import audit
from ..services.local_rank_insights import analyze_local_rank_csv, LocalRankInputError
from ..services.visibility_lab import (
    LabInputError, MODES, import_gsc_csv, plan_experiment,
    propose_questions, provenance_counts, tenant_lab_config,
    validate_tenant_lab_settings,
)
from .auth import list_orgs

router = APIRouter()


def _require_dev_preview():
    if get_settings().is_production:
        raise HTTPException(status_code=404, detail="Module preview is not enabled here.")


def _context(db: TenantSession, principal: Principal, **values):
    base = {"org": principal.org, "role": principal.role,
            "orgs": list_orgs(db, principal.user)}
    base.update(values)
    return base


@router.get("/app/modules")
def module_home(request: Request, db: TenantSession = Depends(get_db),
                principal: Principal = Depends(member())):
    _require_dev_preview()
    return render(request, "app/modules.html",
                  _context(db, principal, modules=catalog(), connection=connection_policy()))


def _lab_config(org):
    return tenant_lab_config(org, pilot_org_id=os.getenv("MF_VISIBILITY_LAB_PILOT_ORG_ID"))


def _tenant_evidence_counts(db: TenantSession) -> dict[str, int]:
    raw = dict(db.execute(select(
        VisibilityObservation.provenance,
        func.count(VisibilityObservation.id),
    ).group_by(VisibilityObservation.provenance)).all())
    counts = {
        "verified_provider": int(raw.get("verified_provider", 0)),
        "captured_unverified": int(raw.get("captured_unverified", 0)),
        "historical_import": int(raw.get("historical_import", 0)),
        "quarantined_reconstruction": int(raw.get("quarantined_reconstruction", 0)),
        "synthetic_fixture": int(raw.get("synthetic_fixture", 0)),
    }
    counts["total"] = sum(counts.values())
    return counts


@router.get("/app/modules/visibility")
def visibility_home(request: Request, db: TenantSession = Depends(get_db),
                    principal: Principal = Depends(member())):
    _require_dev_preview()
    cfg = _lab_config(principal.org)
    return render(request, "app/visibility_lab.html",
                  _context(db, principal, questions=[
                      {"id": f"q-seed-{i}", "text": text}
                      for i, text in enumerate(cfg.questions, 1)],
                      markets=cfg.markets, modes=MODES,
                      brand_aliases=cfg.brand_aliases,
                      is_pilot=cfg.is_pilot,
                      evidence_counts=_tenant_evidence_counts(db)))


@router.get("/app/modules/local-search")
def local_rank_home(request: Request, db: TenantSession = Depends(get_db),
                    principal: Principal = Depends(member())):
    _require_dev_preview()
    return render(request, "app/local_visibility.html", _context(db, principal))


class CsvPreview(BaseModel):
    csv_text: str = Field(min_length=1, max_length=320_000)


@router.post("/api/v1/modules/local-search/analyze")
def local_rank_preview(payload: CsvPreview,
                       principal: Principal = Depends(member_api("read"))):
    _require_dev_preview()
    try:
        return analyze_local_rank_csv(payload.csv_text)
    except LocalRankInputError as exc:
        raise HTTPException(422, detail=str(exc)) from None


class QuestionPreview(BaseModel):
    id: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=300)


class ExperimentPreview(BaseModel):
    questions: list[QuestionPreview] = Field(min_length=1, max_length=200)
    markets: list[str] = Field(min_length=1, max_length=10)
    modes: list[str] = Field(min_length=1, max_length=3)
    repetitions: int = Field(default=1, ge=1, le=10)
    chunk_size: int = Field(default=5, ge=1, le=10)
    max_total: int = Field(default=100, ge=1, le=500)


class LabConfigUpdate(BaseModel):
    markets: list[str] = Field(min_length=1, max_length=10)
    brand_aliases: list[str] | None = Field(default=None, max_length=20)
    questions: list[str] | None = Field(default=None, max_length=200)


class EvidencePreview(BaseModel):
    runs: list[dict] = Field(max_length=1_000)


@router.get("/api/v1/modules/visibility/config")
def get_visibility_config(principal: Principal = Depends(member_api("read"))):
    _require_dev_preview()
    return {"config": _lab_config(principal.org).serialize()}


@router.post("/api/v1/modules/visibility/config")
def update_visibility_config(
    payload: LabConfigUpdate,
    db: TenantSession = Depends(get_db),
    principal: Principal = Depends(member_api("improvements.write")),
):
    _require_dev_preview()
    try:
        validated = validate_tenant_lab_settings(
            business_name=principal.org.name,
            markets=payload.markets,
            brand_aliases=payload.brand_aliases,
            questions=payload.questions,
        )
    except LabInputError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    current_settings = dict(principal.org.settings or {})
    existing_lab = current_settings.get("visibility_lab")
    merged_lab = dict(existing_lab) if isinstance(existing_lab, dict) else {}
    merged_lab.update(validated)
    current_settings["visibility_lab"] = merged_lab
    principal.org.settings = current_settings
    db.add(principal.org)
    db.flush()
    audit(db, principal.org.id, principal.user.id, "visibility.config_updated",
          "organization", principal.org.id,
          {"markets": len(validated["markets"]),
           "custom_questions": len(validated.get("questions", ()))})
    return {"status": "saved", "config": _lab_config(principal.org).serialize()}


@router.post("/api/v1/modules/visibility/gsc/analyze")
def gsc_preview(payload: CsvPreview, principal: Principal = Depends(member_api("read"))):
    _require_dev_preview()
    cfg = _lab_config(principal.org)
    try:
        result = import_gsc_csv(payload.csv_text, config=cfg)
    except LabInputError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    return {
        "status": "local_preview_only", "snapshot": result,
        "suggestions": propose_questions(result["rows"], config=cfg),
        "persistence": "not_saved", "gsc_api_connected": False,
    }


@router.post("/api/v1/modules/visibility/plan")
def experiment_preview(payload: ExperimentPreview,
                       principal: Principal = Depends(member_api("read"))):
    _require_dev_preview()
    cfg = _lab_config(principal.org)
    try:
        return plan_experiment(
            [q.model_dump() for q in payload.questions], payload.markets, payload.modes,
            payload.repetitions, payload.chunk_size, payload.max_total,
            config=cfg,
        )
    except LabInputError as exc:
        raise HTTPException(422, detail=str(exc)) from None


@router.post("/api/v1/modules/visibility/evidence/classify")
def evidence_preview(payload: EvidencePreview,
                     principal: Principal = Depends(member_api("read"))):
    _require_dev_preview()
    return {
        "counts": provenance_counts(payload.runs), "status": "read_only_classification",
        "warning": "Stored run labels alone are not independently verified provider receipts.",
    }
