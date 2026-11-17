"""Owner-consented AI visibility research budget and no-dispatch reservations (MF-036).

Development-only tenant API. These endpoints never contact a model provider.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import TenantSession, get_db
from app.deps import Principal, member_api, parse_uuid
from app.schemas.ai_visibility_run import ExperimentSpec
from app.services import visibility_execution as execution
from app.services.audit import audit

router = APIRouter(prefix="/api/v1/modules/visibility/execution",
                   tags=["Owner research spending consent"])


def _dev_only():
    if get_settings().is_production:
        raise HTTPException(404, "Research execution is not enabled here.")


def _failed(fn):
    try:
        return fn()
    except execution.ResearchNotAuthorized as exc:
        raise HTTPException(403, str(exc)) from None
    except execution.ResearchAlreadyCancelled as exc:
        raise HTTPException(409, str(exc)) from None
    except (execution.ResearchConflict, execution.ResearchBudgetExceeded) as exc:
        # Error details contain no provider credentials or customer PII.
        raise HTTPException(422, str(exc)) from None


class BudgetApproval(BaseModel):
    monthly_ceiling_usd: str = Field(min_length=1, max_length=20)
    confirm: Literal["AUTHORIZE RESEARCH RESERVATIONS"]


class SearchReservation(BaseModel):
    experiment: ExperimentSpec
    planned_run_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    confirm: Literal["RESERVE THIS SEARCH"]


class SearchCancellation(BaseModel):
    confirm: Literal["CANCEL UNSENT SEARCH"]


@router.get("/budget")
def budget(db: TenantSession = Depends(get_db),
           p: Principal = Depends(member_api("read"))):
    _dev_only()
    return _failed(lambda: execution.budget_view(execution.get_current_budget(
        db, p.org.id, p.user.id)))


@router.put("/budget")
def set_budget(body: BudgetApproval, db: TenantSession = Depends(get_db),
               p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()

    def action():
        row, created = execution.authorize_budget(
            db, p.org.id, p.user.id,
            monthly_ceiling_usd=body.monthly_ceiling_usd,
            owner_confirmed=True,
        )
        audit(db, p.org.id, p.user.id, "visibility.research_budget_authorized",
              "visibility_research_budget", row.id,
              {"month": row.period_month.isoformat(),
               "ceiling_micros": row.ceiling_micros})
        return {"created": created, "budget": execution.budget_view(row),
                "no_provider_calls_made": True}
    return _failed(action)


@router.post("/reservations")
def reserve_search(body: SearchReservation, db: TenantSession = Depends(get_db),
                   p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()

    def action():
        row, created = execution.reserve_planned_search(
            db, p.org.id, p.user.id, spec=body.experiment,
            planned_run_id=body.planned_run_id, owner_confirmed=True)
        if created:
            audit(db, p.org.id, p.user.id, "visibility.search_reserved",
                  "visibility_research_reservation", row.id,
                  {"run_id": row.planned_run_id, "estimated_ceiling_micros": row.ceiling_micros})
        return {"created": created, "reservation": execution.reservation_view(row),
                "no_provider_calls_made": True}
    return _failed(action)


@router.get("/reservations")
def reservations(db: TenantSession = Depends(get_db),
                 p: Principal = Depends(member_api("read"))):
    _dev_only()
    return _failed(lambda: {
        "reservations": [
            execution.reservation_view(x)
            for x in execution.list_reservations(db, p.org.id, p.user.id)
        ],
        "no_provider_calls_made": True,
    })


@router.post("/reservations/{reservation_id}/cancel")
def cancel_search(reservation_id: str, body: SearchCancellation,
                  db: TenantSession = Depends(get_db),
                  p: Principal = Depends(member_api("improvements.write"))):
    _dev_only()

    def action():
        row, changed = execution.cancel_reserved_search(
            db, p.org.id, p.user.id, parse_uuid(reservation_id))
        if changed:
            audit(db, p.org.id, p.user.id, "visibility.search_reservation_cancelled",
                  "visibility_research_reservation", row.id,
                  {"run_id": row.planned_run_id})
        return {"cancelled_now": changed, "reservation": execution.reservation_view(row),
                "no_provider_calls_made": True}
    return _failed(action)
