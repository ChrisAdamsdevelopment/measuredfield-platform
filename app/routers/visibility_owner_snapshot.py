"""Private owner-readable AI visibility evidence integrity page and API.

This is local-preview-only, strictly tenant scoped and read-only. Never
interpret user CSV/source labels as independently verified provider results.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, member, member_api, render
from ..services.visibility_owner_snapshot import owner_evidence_health
from .auth import list_orgs

router = APIRouter()


def _local_preview_only() -> None:
    if get_settings().is_production:
        raise HTTPException(404, "This preview is not available here.")


@router.get("/app/modules/visibility/evidence-health")
def visibility_evidence_health(
    request: Request,
    db: TenantSession = Depends(get_db),
    principal: Principal = Depends(member()),
):
    _local_preview_only()
    summary = owner_evidence_health(db, principal.org.id)
    return render(
        request,
        "app/visibility_owner_snapshot.html",
        {
            "org": principal.org,
            "role": principal.role,
            "orgs": list_orgs(db, principal.user),
            "summary": summary,
        },
    )


@router.get("/api/v1/modules/visibility/evidence-health")
def visibility_evidence_health_json(
    db: TenantSession = Depends(get_db),
    principal: Principal = Depends(member_api("read")),
):
    _local_preview_only()
    return {
        "status": "local_owner_preview",
        "summary": owner_evidence_health(db, principal.org.id),
        "provider_requests_sent": 0,
        "source_of_truth": "tenant_scoped_saved_observations",
    }
