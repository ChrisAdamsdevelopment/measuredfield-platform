"""Owner-initiated direct Stripe Payment Link claim.

Every state change depends on an authenticated owner of the active server-selected
workspace, a verified account mailbox, a signed Stripe purchase and a fresh
provider subscription status check. Purchase IDs alone are never capabilities.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from ..db import TenantSession, get_db
from ..deps import Principal, back, member, member_post, operator, operator_post, parse_uuid, render
from ..security import rate_limited
from ..services import payment_link_claims as purchases
from .workspace import _page

router = APIRouter()


@router.get("/purchase/claim")
def public_purchase_claim(request: Request):
    return render(request, "public/claim_purchase.html")


@router.get("/app/billing/claim")
def owner_claim_list(request: Request, db: TenantSession = Depends(get_db),
                     p: Principal = Depends(member("billing.read"))):
    verified = purchases.verified_mailbox(db, p.user.id, p.user.email)
    claims = purchases.eligible(db, p.user.id, p.user.email) if p.role == "owner" else []
    return _page(request, db, p, "app/claim_purchase.html", {
        "claims": claims, "verified_email": verified, "is_owner": p.role == "owner",
    })


@router.post("/app/billing/claim/{claim_id}")
def owner_claim_purchase(claim_id: str, db: TenantSession = Depends(get_db),
                         p: Principal = Depends(member_post("billing.read")),
                         confirm: str = Form("")):
    if p.role != "owner" or confirm != "yes":
        return back("/app/billing/claim", err="Confirm purchase ownership as a workspace owner.")
    if rate_limited(db, "purchase-claim:" + str(p.user.id), limit=10, window_seconds=3600):
        return back("/app/billing/claim", err="Too many attempts. Try again later.")
    try:
        purchases.claim_to_owner_workspace(
            db, claim_id=parse_uuid(claim_id), user_id=p.user.id, org_id=p.org.id)
    except purchases.PaymentLinkRetry:
        # No consumed claim or entitlement if Stripe cannot confirm live status.
        return back("/app/billing/claim", err="Stripe verification is temporarily unavailable.")
    except purchases.PaymentLinkDenied:
        # Generic response prevents identifying purchases belonging to another user.
        return back("/app/billing/claim", err="This purchase cannot be claimed in this workspace.")
    return back("/app/settings", ok="Purchase connected to this workspace.")


@router.get("/operator/payment-link-claims")
def payment_link_support_queue(request: Request, db: TenantSession = Depends(get_db),
                               p: Principal = Depends(operator)):
    db.set_context(None, p.user.id)
    return render(request, "operator/payment_link_claims.html", {
        "claims": purchases.support_queue(db),
    })


@router.post("/operator/payment-link-claims/{claim_id}/recheck")
def payment_link_support_recheck(claim_id: str, reason: str = Form(""),
                                 db: TenantSession = Depends(get_db),
                                 p: Principal = Depends(operator_post)):
    if rate_limited(db, "payment-link-operator-review:" + str(p.user.id),
                    limit=20, window_seconds=3600):
        return back("/operator/payment-link-claims", err="Review rate limit reached.")
    db.set_context(None, p.user.id)
    try:
        verdict = purchases.operator_recheck(
            db, actor_user_id=p.user.id, claim_id=parse_uuid(claim_id),
            reason=reason)
    except purchases.PaymentLinkRetry:
        return back("/operator/payment-link-claims",
                    err="Stripe re-verification is unavailable. No claim was reopened.")
    except purchases.PaymentLinkDenied:
        return back("/operator/payment-link-claims",
                    err="This claim cannot be reopened. Check purchase evidence.")
    if verdict == "reopened":
        return back("/operator/payment-link-claims",
                    ok="Provider verified. This purchase is pending the buyer's separate owner claim.")
    return back("/operator/payment-link-claims",
                ok="Purchase retained for support review; no entitlement was granted.")
