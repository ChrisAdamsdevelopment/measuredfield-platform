"""Purchase intent from Stripe's existing direct Payment Links.

Fail-closed. The verified signed event authorizes recording a pending purchase,
not any tenant, user or entitlement. A different authenticated owner action
binds a purchase to ONE existing, verified-email workspace. Nothing is
provisioned from a browser redirect or Stripe customer email alone.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import TenantSession
from ..models import (AuthToken, BillingSubscription, PendingPaymentLinkClaim,
                      PaymentLinkClaimReview, ServiceOrder, User, now)
from ..security import normalize_email, valid_email
from . import jobs, payments
from .orders import _checkout_total, _period_end, _reconcile_subscription, active_subscription_for
from .audit import audit
from .email import queue_email

# Only these two VERIFIED Stripe Payment Link IDs can create claimable purchases.
# Any other link requires a reviewed catalog change, never inference from email.
@dataclass(frozen=True)
class LinkOffer:
    price_id: str
    offer_code: str
    title: str
    requires_operate: bool = False


LINKS = {
    "plink_1UcxSA2LWll6Mkhu7MR1P3dr": LinkOffer(
        "price_1Ub6rZ2LWll6MkhusWBI1ebk", "operate", "Business Workspace"),
    "plink_1UcxUD2LWll6MkhuecqI4wT9": LinkOffer(
        "price_1Ub6rh2LWll6MkhuYiaLXu2m", "growth_partner", "Growth Partner", True),
}
PRICE_CENTS = 9900
STRIPE_ID = re.compile(r"^(?:cs|sub|cus)_[A-Za-z0-9_-]{8,220}$")


class PaymentLinkRetry(RuntimeError):
    """Temporary Stripe verification failure: webhook MUST return 503 and roll back."""


class PaymentLinkDenied(ValueError):
    """Denied account or purchase claim (no tenant entitlement)."""


def _provider_get(provider, path: str, params: dict | None = None) -> dict:
    """Fixed Stripe API origin; no request URL or IDs supplied by the customer."""
    try:
        response = provider.client.get(
            "https://api.stripe.com/v1/" + path,
            params=params or None, auth=(provider.secret_key, ""),
            timeout=10, follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise PaymentLinkRetry("Stripe verification temporarily unavailable") from exc
    if response.status_code != 200:
        raise PaymentLinkRetry("Stripe verification temporarily unavailable")
    try:
        result = response.json()
    except (ValueError, TypeError) as exc:
        raise PaymentLinkRetry("Stripe returned invalid verification data") from exc
    if not isinstance(result, dict):
        raise PaymentLinkRetry("Stripe returned invalid verification data")
    return result


def _stripe_subscription(provider, stripe_id: str, customer_id: str) -> tuple[str, datetime] | None:
    if (not isinstance(stripe_id, str) or not STRIPE_ID.fullmatch(stripe_id)
            or not stripe_id.startswith("sub_")):
        return None
    data = _provider_get(provider, "subscriptions/" + stripe_id)
    if data.get("id") != stripe_id or data.get("customer") != customer_id:
        return None
    if data.get("status") not in ("active", "trialing"):
        return None
    end = _period_end(data.get("current_period_end"))
    if end is None:
        items = (data.get("items") or {}).get("data") or []
        if len(items) == 1 and isinstance(items[0], dict):
            end = _period_end(items[0].get("current_period_end"))
    if end is None or end <= now():
        return None
    return data["status"], end


def _line_item_matches(provider, session_id: str, expected_price: str) -> bool:
    result = _provider_get(
        provider, "checkout/sessions/" + session_id + "/line_items", {"limit": 2})
    items = result.get("data")
    # Require Stripe's actual boolean False, not a missing or truthy pagination flag.
    # A paginated/ambiguous response could hide a second purchased line item.
    if (not isinstance(items, list) or len(items) != 1 or result.get("has_more") is not False):
        return False
    line = items[0]
    if not isinstance(line, dict) or type(line.get("quantity")) is not int or line["quantity"] != 1:
        return False
    price = line.get("price")
    return (isinstance(price, dict) and price.get("id") == expected_price
            and price.get("currency") == "usd"
            and price.get("unit_amount") == PRICE_CENTS
            and (price.get("recurring") or {}).get("interval") == "month")


def from_signed_checkout(db: TenantSession, event: dict, obj: dict) -> str:
    """Runs only after HTTP Stripe-Signature verification and event deduplication."""
    offer = LINKS.get(obj.get("payment_link"))
    if offer is None:
        return "not_an_allowlisted_payment_link"
    session_id, customer, subscription = (obj.get("id"), obj.get("customer"),
                                          obj.get("subscription"))
    if (not isinstance(session_id, str) or not STRIPE_ID.fullmatch(session_id)
            or not session_id.startswith("cs_")
            or not isinstance(customer, str) or not STRIPE_ID.fullmatch(customer)
            or not customer.startswith("cus_")
            or not isinstance(subscription, str) or not STRIPE_ID.fullmatch(subscription)
            or not subscription.startswith("sub_")
            or obj.get("mode") != "subscription" or obj.get("currency") != "usd"):
        return "direct_checkout_invalid_identity"
    details = obj.get("customer_details")
    raw_email = details.get("email") if isinstance(details, dict) else None
    email = normalize_email(raw_email) if isinstance(raw_email, str) else ""
    if not valid_email(email):
        return "direct_checkout_missing_email"
    breakdown = _checkout_total(obj, PRICE_CENTS)
    if breakdown is None or obj.get("amount_subtotal") is None:
        return "direct_checkout_invalid_amount"
    amount, discount, tax = breakdown
    expected_status = "no_payment_required" if amount == 0 else "paid"
    if obj.get("payment_status") != expected_status:
        return "direct_checkout_not_paid"

    existing = db.scalar(select(PendingPaymentLinkClaim).where(
        PendingPaymentLinkClaim.checkout_session_id == session_id).with_for_update())
    if existing is not None:
        return "direct_checkout_already_pending"

    provider = payments.get_provider()
    if provider is None:
        raise PaymentLinkRetry("Stripe verification not configured")
    if not _line_item_matches(provider, session_id, offer.price_id):
        return "direct_checkout_price_mismatch"
    state = _stripe_subscription(provider, subscription, customer)
    if state is None:
        return "direct_checkout_inactive_subscription"
    status, period_end = state
    conflict = db.scalar(select(PendingPaymentLinkClaim.id).where(
        PendingPaymentLinkClaim.stripe_subscription_id == subscription))
    if conflict is not None:
        return "direct_checkout_subscription_conflict"
    db.add(PendingPaymentLinkClaim(
        checkout_session_id=session_id, stripe_subscription_id=subscription,
        stripe_customer_id=customer, stripe_payment_link_id=obj["payment_link"],
        stripe_price_id=offer.price_id, offer_code=offer.offer_code,
        billing_email=email, amount_subtotal_cents=int(obj["amount_subtotal"]),
        amount_total_cents=amount, discount_cents=discount, tax_cents=tax,
        stripe_event_id=event["id"], subscription_status=status,
        period_end=period_end, status="pending_claim", verified_at=now(),
    ))
    # Notice is not an authorization credential; never embed bearer links.
    settings = get_settings()
    if settings.email_provider != "none":
        msg = queue_email(
            db, kind="billing_claim", to=email,
            subject="Your MeasuredField purchase is ready to connect",
            body=("Your Stripe purchase was received. Sign in to your verified "
                  "MeasuredField account, select your business, and open "
                  + settings.public_base_url.rstrip("/") + "/app/billing/claim. "
                  "If you did not make this purchase, contact support."),
        )
        jobs.enqueue(db, "send_email", {"message_id": str(msg.id)},
                     key="email:" + str(msg.id))
    return "direct_checkout_pending_claim"


def verified_mailbox(db: TenantSession, user_id: uuid.UUID, email: str) -> bool:
    """Accepted email-delivered invite or reset for THIS account's email.

    A username/password alone does not prove control of Stripe billing email.
    """
    from ..models import User
    user = db.get(User, user_id)
    if user is None or user.disabled_at is not None or user.email != email:
        return False
    proof = db.scalar(select(AuthToken.id).where(
        AuthToken.email == email, AuthToken.purpose.in_(("invite", "reset")),
        AuthToken.used_at.is_not(None), AuthToken.issued_via == "email").limit(1))
    return proof is not None


def eligible(db: TenantSession, user_id: uuid.UUID, email: str) -> list[PendingPaymentLinkClaim]:
    if not verified_mailbox(db, user_id, email):
        return []
    return db.scalars(select(PendingPaymentLinkClaim).where(
        PendingPaymentLinkClaim.billing_email == email,
        PendingPaymentLinkClaim.status == "pending_claim",
    ).order_by(PendingPaymentLinkClaim.verified_at.desc()).limit(20)).all()


def claim_to_owner_workspace(db: TenantSession, *, claim_id: uuid.UUID,
                             user_id: uuid.UUID, org_id: uuid.UUID) -> ServiceOrder:
    """Explicit membership checked by caller; platform claim guarded again here."""
    from ..models import Membership, Organization
    membership = db.scalar(select(Membership).where(
        Membership.user_id == user_id, Membership.org_id == org_id))
    if membership is None or membership.role != "owner":
        raise PaymentLinkDenied("Only the owner of this workspace can claim purchases")
    org = db.get(Organization, org_id)
    if org is None or org.status != "active":
        raise PaymentLinkDenied("This workspace cannot accept purchases")
    claim = db.scalar(select(PendingPaymentLinkClaim).where(
        PendingPaymentLinkClaim.id == claim_id).with_for_update())
    if claim is None or claim.status != "pending_claim" or claim.order_id is not None:
        raise PaymentLinkDenied("Purchase is unavailable or already claimed")
    if not verified_mailbox(db, user_id, claim.billing_email):
        raise PaymentLinkDenied("Verify the purchase email before claiming")
    offer = LINKS.get(claim.stripe_payment_link_id)
    if (offer is None or offer.offer_code != claim.offer_code
            or offer.price_id != claim.stripe_price_id):
        raise PaymentLinkDenied("Purchase details are inconsistent")
    if offer.requires_operate and not active_subscription_for(db, org_id, "operate"):
        raise PaymentLinkDenied("Growth Partner requires an active Business Workspace subscription")
    if offer.offer_code == "operate" and active_subscription_for(db, org_id, "operate"):
        raise PaymentLinkDenied("This workspace already has an active Business Workspace subscription")
    conflicting = db.scalar(select(BillingSubscription).where(
        BillingSubscription.org_id == org_id,
        BillingSubscription.stripe_customer_id.is_not(None),
    ).limit(1))
    if conflicting is not None and conflicting.stripe_customer_id != claim.stripe_customer_id:
        raise PaymentLinkDenied("This workspace is linked to a different Stripe customer")

    provider = payments.get_provider()
    if provider is None:
        raise PaymentLinkRetry("Stripe verification unavailable")
    verified_state = _stripe_subscription(provider, claim.stripe_subscription_id,
                                          claim.stripe_customer_id)
    if verified_state is None:
        claim.status = "expired"
        raise PaymentLinkDenied("Subscription is no longer active")
    status, period_end = verified_state
    existing_sub = db.scalar(select(BillingSubscription).where(
        BillingSubscription.stripe_subscription_id == claim.stripe_subscription_id
    ).with_for_update())
    if existing_sub is not None and (existing_sub.order_id is not None
                                     or existing_sub.org_id not in (None, org_id)
                                     or existing_sub.stripe_customer_id not in (None, claim.stripe_customer_id)
                                     or existing_sub.status in ("canceled", "incomplete_expired")
                                     or existing_sub.invoice_status == "refunded"):
        raise PaymentLinkDenied("Subscription has conflicting billing history")

    # The Stripe signed event was validated independently before a pending row
    # existed. Claimed orders are NEVER created from client-submitted financial data.
    order = ServiceOrder(
        assessment_id=None, org_id=org_id, offer_code=offer.offer_code,
        offer_snapshot={"name": offer.title, "code": offer.offer_code,
                        "price_cents": PRICE_CENTS, "billing": "monthly",
                        "stripe_price_id": offer.price_id,
                        "checkout_origin": "direct_payment_link"},
        amount_cents=PRICE_CENTS, status="provisioned",
        approved_name="Verified workspace owner", approved_at=now(),
        terms_version=get_settings().terms_version,
        payment_provider="stripe", provider_ref=claim.checkout_session_id,
        payment_evidence="stripe:" + claim.stripe_event_id,
        settled_amount_cents=claim.amount_total_cents,
        discount_amount_cents=claim.discount_cents, tax_amount_cents=claim.tax_cents,
        paid_at=now(), provisioned_at=now(),
    )
    db.add(order)
    db.flush()
    if existing_sub is None:
        existing_sub = BillingSubscription(
            stripe_subscription_id=claim.stripe_subscription_id,
            stripe_customer_id=claim.stripe_customer_id,
        )
        db.add(existing_sub)
        db.flush()
    existing_sub.org_id, existing_sub.order_id = org_id, order.id
    existing_sub.stripe_customer_id = claim.stripe_customer_id
    existing_sub.status, existing_sub.period_end = status, period_end
    claim.order_id, claim.org_id = order.id, org_id
    claim.claimed_by_user_id, claim.claimed_at = user_id, now()
    claim.status = "claimed"
    _reconcile_subscription(db, existing_sub)
    audit(db, org_id, user_id, "billing.payment_link_claimed",
          "service_order", order.id, {"offer_code": offer.offer_code})
    return order


def support_queue(db: TenantSession) -> list[PendingPaymentLinkClaim]:
    """Platform-only list. Caller must have already required operator identity."""
    return db.scalars(select(PendingPaymentLinkClaim).where(
        PendingPaymentLinkClaim.status.in_(("expired", "needs_review"))
    ).order_by(PendingPaymentLinkClaim.verified_at.desc()).limit(100)).all()


def operator_recheck(db: TenantSession, *, actor_user_id: uuid.UUID,
                     claim_id: uuid.UUID, reason: str) -> str:
    """Human review of a disputed claim. Reopening does NOT grant access.

    A signed terminal cancellation in the event ledger always wins over a
    potentially stale API response. Customer must still complete separate,
    verified owner claim after any successful reopening.
    """
    actor = db.get(User, actor_user_id)
    if actor is None or actor.disabled_at is not None or not actor.is_platform_admin:
        raise PaymentLinkDenied("Operator authentication is required")
    reason = " ".join((reason or "").split())[:300]
    if len(reason) < 12:
        raise PaymentLinkDenied("A review reason of at least 12 characters is required")
    row = db.scalar(select(PendingPaymentLinkClaim).where(
        PendingPaymentLinkClaim.id == claim_id).with_for_update())
    if (row is None or row.status not in ("expired", "needs_review")
            or row.order_id is not None or row.org_id is not None):
        raise PaymentLinkDenied("Only unclaimed disputed purchases may be rechecked")
    offer = LINKS.get(row.stripe_payment_link_id)
    if offer is None or offer.price_id != row.stripe_price_id or offer.offer_code != row.offer_code:
        raise PaymentLinkDenied("The purchase catalog identity is not recognized")
    provider = payments.get_provider()
    if provider is None:
        raise PaymentLinkRetry("Stripe verification unavailable")
    state = _stripe_subscription(provider, row.stripe_subscription_id,
                                 row.stripe_customer_id)
    prior = db.scalar(select(BillingSubscription).where(
        BillingSubscription.stripe_subscription_id == row.stripe_subscription_id))
    terminal = bool(prior and (
        prior.status in ("canceled", "incomplete_expired")
        or prior.invoice_status == "refunded" or prior.order_id is not None
        or prior.org_id is not None
        or prior.stripe_customer_id not in (None, row.stripe_customer_id)))
    if state is None or terminal:
        row.status = "needs_review"
        decision = "needs_review"
    else:
        row.subscription_status, row.period_end = state
        row.verified_at, row.status = now(), "pending_claim"
        decision = "reopened"
    db.add(PaymentLinkClaimReview(
        claim_id=row.id, reviewer_user_id=actor_user_id, action="recheck",
        outcome=decision, reason=reason))
    return decision
