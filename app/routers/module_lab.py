"""Private, development-only MeasuredField module suite. No paid or device actions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, member, member_api, render
from ..module_catalog import catalog, connection_policy
from ..services.local_rank_insights import analyze_local_rank_csv, LocalRankInputError
from ..services.visibility_lab import (
    BENCHMARK_22, LabInputError, MARKETS, MODES, import_gsc_csv,
    plan_experiment, propose_questions, provenance_counts,
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


@router.get("/app/modules/visibility")
def visibility_home(request: Request, db: TenantSession = Depends(get_db),
                    principal: Principal = Depends(member())):
    _require_dev_preview()
    return render(request, "app/visibility_lab.html",
                  _context(db, principal, questions=[
                      {"id": f"q-seed-{i}", "text": text}
                      for i, text in enumerate(BENCHMARK_22, 1)],
                      markets=MARKETS, modes=MODES))


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
    markets: list[str] = Field(min_length=1, max_length=5)
    modes: list[str] = Field(min_length=1, max_length=3)
    repetitions: int = Field(default=1, ge=1, le=10)
    chunk_size: int = Field(default=5, ge=1, le=10)
    max_total: int = Field(default=100, ge=1, le=500)


class EvidencePreview(BaseModel):
    runs: list[dict] = Field(max_length=1_000)


@router.post("/api/v1/modules/visibility/gsc/analyze")
def gsc_preview(payload: CsvPreview, principal: Principal = Depends(member_api("read"))):
    _require_dev_preview()
    try:
        result = import_gsc_csv(payload.csv_text)
    except LabInputError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    return {
        "status": "local_preview_only", "snapshot": result,
        "suggestions": propose_questions(result["rows"]),
        "persistence": "not_saved", "gsc_api_connected": False,
    }


@router.post("/api/v1/modules/visibility/plan")
def experiment_preview(payload: ExperimentPreview,
                       principal: Principal = Depends(member_api("read"))):
    _require_dev_preview()
    try:
        return plan_experiment(
            [q.model_dump() for q in payload.questions], payload.markets, payload.modes,
            payload.repetitions, payload.chunk_size, payload.max_total,
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
