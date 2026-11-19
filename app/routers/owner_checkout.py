"""Existing signed-in workspace owner -> normal Stripe Checkout, no duplicate tenant.

This is a subscription purchase path, not an authorization shortcut. Only signed
webhooks bind the order to a Stripe subscription. A return URL does not grant access.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, back, member_post
from ..models import Organization, ServiceOrder, now
from ..security import rate_limited
from ..services import orders, payments
from ..services.audit import audit

router = APIRouter()


@router.post("/app/billing/checkout")
def owner_workspace_checkout(db: TenantSession = Depends(get_db),
                             p: Principal = Depends(member_post("billing.read"))):
    if p.role != "owner":
        return back("/app/settings", err="Only a business owner can start workspace checkout.")
    settings = get_settings()
    offer = settings.offer("operate")
    if (settings.payment_provider != "stripe" or not settings.stripe_secret_key
            or not settings.stripe_webhook_secret or not offer or offer.get("billing") != "monthly"
            or not offer.get("stripe_price_id") or offer.get("allow_promotion_codes") is not True):
        return back("/app/settings", err="Workspace checkout is not configured.")

    # Serialize requests for the current organization. The browser may not pick
    # the organization using untrusted form values; session membership determines it.
    org = db.scalar(select(Organization).where(Organization.id == p.org.id).with_for_update())
    if org is None or org.status != "active":
        return back("/app/settings", err="This workspace is unavailable.")
    if orders.active_subscription_for(db, org.id, "operate"):
        return back("/app/settings", ok="This workspace already has an active subscription.")
    if orders.pending_owner_checkout(db, org.id) is not None:
        return back("/app/settings", err="A checkout is already pending for this workspace.")
    if rate_limited(db, "owner-checkout:" + str(p.user.id), limit=6, window_seconds=3600):
        return back("/app/settings", err="Too many checkout attempts. Please try later.")

    order = ServiceOrder(
        assessment_id=None, org_id=org.id,
        offer_code="operate", offer_snapshot={**offer, "checkout_origin": "owner_workspace"},
        amount_cents=int(offer["price_cents"]),
        status="approved_unpaid", approved_name=(p.user.name or "Workspace owner")[:120],
        approved_at=now(), terms_version=settings.terms_version,
    )
    db.add(order)
    db.flush()
    provider = payments.get_provider(settings)
    if provider is None:
        order.status = "cancelled"
        return back("/app/settings", err="Stripe checkout is unavailable.")
    try:
        checkout = provider.create_checkout(
            order_id=str(order.id), offer=order.offer_snapshot,
            customer_email=p.user.email,
            success_url=settings.public_base_url.rstrip("/") + "/app/settings",
            cancel_url=settings.public_base_url.rstrip("/") + "/app/settings",
        )
    except payments.PaymentError:
        order.status = "cancelled"
        audit(db, org.id, p.user.id, "billing.checkout_unavailable", "service_order", order.id)
        return back("/app/settings", err="Stripe checkout could not start. No payment was taken.")
    # Strictly redirect only to Stripe's hosted Checkout origin. Never trust a
    # third-party URL returned by a misconfigured/compromised payment adapter.
    from urllib.parse import urlsplit
    target = checkout["url"]
    parsed = urlsplit(target)
    if parsed.scheme != "https" or parsed.hostname != "checkout.stripe.com" or parsed.username or parsed.password:
        order.status = "cancelled"
        return back("/app/settings", err="Stripe returned an invalid checkout address.")
    order.provider_ref = checkout["id"]
    order.payment_provider = "stripe"
    order.status = "payment_pending"
    audit(db, org.id, p.user.id, "billing.checkout_started", "service_order", order.id,
          {"offer_code": "operate", "client_reference_bound": True})
    result = RedirectResponse(target, status_code=303)
    result.headers["Referrer-Policy"] = "no-referrer"
    result.headers["Cache-Control"] = "no-store"
    return result
