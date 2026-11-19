"""Stripe-hosted billing management: only the verified business owner may launch."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, back, member_post
from ..services import billing_portal
from ..security import rate_limited

router = APIRouter()


@router.post("/app/billing/portal")
def launch_portal(db: TenantSession = Depends(get_db),
                  p: Principal = Depends(member_post("billing.read"))):
    if p.role != "owner":
        return back("/app/settings", err="Only a workspace owner can manage billing.")
    settings = get_settings()
    if settings.payment_provider != "stripe" or not settings.stripe_secret_key:
        return back("/app/settings", err="Online billing management is unavailable.")
    customer_id = billing_portal.verified_customer_for(db, p.org.id)
    if customer_id is None:
        return back("/app/settings", err="No verified Stripe billing account is linked to this workspace.")
    if rate_limited(db, "stripe-portal:" + str(p.user.id), limit=10, window_seconds=60):
        db.commit()
        return back("/app/settings", err="Too many billing requests. Try again in a minute.")
    try:
        session_url = billing_portal.create_portal_session(
            customer_id, return_url=settings.public_base_url.rstrip("/") + "/app/settings",
            stripe_secret_key=settings.stripe_secret_key,
        )
    except billing_portal.BillingPortalError:
        return back("/app/settings", err="Stripe billing management could not be opened. Please try again.")
    result = RedirectResponse(session_url, status_code=303)
    result.headers["Referrer-Policy"] = "no-referrer"
    result.headers["Cache-Control"] = "no-store"
    return result
