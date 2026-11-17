"""MF-053 authenticated development-only Guided Growth Launch.

Not a model key endpoint, research executor or account-connection service.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, member, member_api, render
from ..services.growth_journey import GrowthJourneyInput, plan_growth
from .auth import list_orgs


router = APIRouter()


def _private_preview() -> None:
    if get_settings().is_production:
        raise HTTPException(404, detail="This preview is not enabled here.")


@router.get("/app/growth-start")
def owner_growth_start(request: Request, db: TenantSession = Depends(get_db),
                       principal: Principal = Depends(member())):
    _private_preview()
    return render(request, "app/growth_journey.html", {
        "org": principal.org,
        "role": principal.role,
        "orgs": list_orgs(db, principal.user),
        "principal": principal,
    })


@router.post("/api/v1/growth-start/plan")
async def growth_plan(request: Request,
                      principal: Principal = Depends(member_api("read"))):
    _private_preview()
    # Never let the framework reflect unknown fields (e.g., an accidentally
    # pasted API key) in its default 422 response. Reject all invalid input
    # with a generic message and avoid logging the body or its values.
    raw = await request.body()
    if len(raw) > 12_000:
        raise HTTPException(413, detail="This setup request is too large.")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("object required")
        payload = GrowthJourneyInput.model_validate(data)
    except (ValueError, TypeError, ValidationError):
        raise HTTPException(422, detail="Please review the setup choices and try again.") from None
    # This endpoint never reads credentials, invokes a model or spends credits.
    return plan_growth(payload)
