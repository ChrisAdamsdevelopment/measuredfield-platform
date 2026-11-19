"""Existing signed-in workspace owner -> normal Stripe Checkout, no duplicate tenant.

Only verified Stripe webhooks bind paid subscriptions; a successful browser
redirect or founder promotion code never grants access by itself.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
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
SESSION_RE = re.compile(r"^cs_[A-Za-z0-9_-]{8,220}$")


def _stripe_url(url: str) -> bool:
    """No open redirect to a look-alike or provider-controlled URL."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.netloc == "checkout.stripe.com"
            and parsed.path.startswith("/c/pay/") and not parsed.username
            and not parsed.password)


def _stripe_redirect(url: str) -> RedirectResponse:
    result = RedirectResponse(url, status_code=303)
    result.headers["Referrer-Policy"] = "no-referrer"
    result.headers["Cache-Control"] = "no-store"
    return result


def _inspect_stripe_checkout(provider: payments.StripeProvider,
                             order: ServiceOrder) -> dict | None:
    """Reconcile a pending Session directly with Stripe before any retry.

    Look up exactly our server-bound Checkout id at Stripe's fixed API origin.
    Never accept browser-supplied ids, tenant ids, URLs, or payment status.
    """
    session_id = order.provider_ref or ""
    if not SESSION_RE.fullmatch(session_id):
        return None
    try:
        response = provider.client.get(
            "https://api.stripe.com/v1/checkout/sessions/" + session_id,
            auth=(provider.secret_key, ""), timeout=15,
        )
        if response.status_code != 200:
            return None
        info = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if (not isinstance(info, dict) or info.get("id") != session_id
            or info.get("client_reference_id") != str(order.id)
            or info.get("mode") != "subscription"):
        return None
    status = info.get("status")
    if status == "expired" and info.get("payment_status") != "paid":
        return {"status": "expired"}
    if status == "complete":
        return {"status": "complete"}
    if (status == "open" and info.get("payment_status") == "unpaid"
            and _stripe_url(info.get("url"))):
        return {"status": "open", "url": info["url"]}
    return None


@router.post("/app/billing/checkout")
def owner_workspace_checkout(db: TenantSession = Depends(get_db),
                             p: Principal = Depends(member_post("billing.read"))):
    if p.role != "owner":
        return back("/app/settings", err="Only a business owner can start workspace checkout.")
    settings = get_settings()
    offer = settings.offer("operate")
    if (settings.payment_provider != "stripe" or not settings.stripe_secret_key
            or not settings.stripe_webhook_secret or not offer or offer.get("billing") != "monthly"
            or not offer.get("stripe_price_id") or offer.get("allow_promotion_codes") is not True
            or type(offer.get("price_cents")) is not int or offer["price_cents"] <= 0):
        return back("/app/settings", err="Workspace checkout is not configured.")

    # Serialize requests for this authenticated organization. All IDs come
    # from server-side session/membership, never a posted client org parameter.
    org = db.scalar(select(Organization).where(Organization.id == p.org.id).with_for_update())
    if org is None or org.status != "active":
        return back("/app/settings", err="This workspace is unavailable.")
    if orders.active_subscription_for(db, org.id, "operate"):
        return back("/app/settings", ok="This workspace already has an active subscription.")
    if rate_limited(db, "owner-checkout:" + str(p.user.id), limit=12, window_seconds=3600):
        return back("/app/settings", err="Too many checkout attempts. Please try later.")
    provider = payments.get_provider(settings)
    if provider is None:
        return back("/app/settings", err="Stripe checkout is unavailable.")

    outstanding = orders.pending_owner_checkout(db, org.id)
    if outstanding is not None:
        if outstanding.status != "payment_pending":
            return back("/app/settings", err="Stripe is still reconciling your subscription. Contact support before restarting checkout.")
        existing = _inspect_stripe_checkout(provider, outstanding)
        if existing is None:
            return back("/app/settings", err="Unable to verify your previous Stripe checkout. No second checkout was started.")
        if existing["status"] == "open":
            return _stripe_redirect(existing["url"])
        if existing["status"] == "complete":
            return back("/app/settings", ok="Stripe checkout completed. Waiting for verified subscription processing.")
        # The exact previous provider session has been independently verified
        # expired and cannot complete later; it is safe to start a new one.
        outstanding.status = "cancelled"
        audit(db, org.id, p.user.id, "billing.checkout_expired_verified",
              "service_order", outstanding.id)

    order = ServiceOrder(
        assessment_id=None, org_id=org.id,
        offer_code="operate", offer_snapshot={**offer, "checkout_origin": "owner_workspace"},
        amount_cents=offer["price_cents"],
        status="approved_unpaid", approved_name=(p.user.name or "Workspace owner")[:120],
        approved_at=now(), terms_version=settings.terms_version,
    )
    db.add(order)
    db.flush()
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
    if not SESSION_RE.fullmatch(checkout["id"]) or not _stripe_url(checkout["url"]):
        order.status = "cancelled"
        return back("/app/settings", err="Stripe returned an invalid checkout session.")
    order.provider_ref = checkout["id"]
    order.payment_provider = "stripe"
    order.status = "payment_pending"
    audit(db, org.id, p.user.id, "billing.checkout_started", "service_order", order.id,
          {"offer_code": "operate", "client_reference_bound": True})
    return _stripe_redirect(checkout["url"])
