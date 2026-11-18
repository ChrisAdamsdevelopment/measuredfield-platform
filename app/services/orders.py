"""Service orders: customer approval -> verified payment (or recorded exception) -> workspace."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..config import get_settings
from ..db import TenantSession
from ..models import (Assessment, AssessmentReport, BillingSubscription, Improvement, Membership,
                      Organization, PaymentEvent, ServiceOrder, User, now)
from ..security import issue_token, normalize_email
from . import jobs
from .assessment import improvements_from
from .audit import audit
from .email import queue_email


class OrderError(ValueError):
    pass


def create_order(db: TenantSession, assessment: Assessment, offer_code: str, approved_name: str) -> ServiceOrder:
    settings = get_settings()
    offer = settings.offer(offer_code)
    if offer is None:
        raise OrderError("Choose one of the listed options.")
    if offer.get("add_on"):
        raise OrderError("This extra service can only be purchased from an existing workspace.")
    approved_name = " ".join((approved_name or "").split())[:120]
    if len(approved_name) < 3:
        raise OrderError("Type your full name to confirm.")
    free = int(offer["price_cents"]) == 0
    order = ServiceOrder(
        assessment_id=assessment.id, offer_code=offer_code, offer_snapshot=offer,
        amount_cents=int(offer["price_cents"]),
        status="consult_requested" if free else "approved_unpaid",
        approved_name=approved_name, approved_at=now(), terms_version=settings.terms_version)
    db.add(order)
    db.flush()
    assessment.status = "ordered"
    return order


def mark_paid(db: TenantSession, order: ServiceOrder, *, provider: str, evidence: str) -> bool:
    """Record a payment that has real evidence behind it. Returns False if already paid."""
    if order.status in ("paid", "provisioned"):
        return False
    if order.status not in ("approved_unpaid", "payment_pending"):
        raise OrderError("This order cannot be paid in its current state.")
    if not evidence or len(evidence.strip()) < 4:
        raise OrderError("A payment reference is required.")
    order.status, order.paid_at = "paid", now()
    order.payment_provider, order.payment_evidence = provider, evidence.strip()[:255]
    jobs.enqueue(db, "provision_order", {"order_id": str(order.id)}, key=f"provision:{order.id}")
    return True


_SUBSCRIPTION_STATES = {
    "active", "trialing", "past_due", "unpaid", "incomplete",
    "incomplete_expired", "canceled", "paused", "pending",
}


def _event_time(event: dict) -> int:
    """Stripe event.created is a Unix timestamp. Missing/invalid times are oldest."""
    try:
        return max(0, int(event.get("created", 0)))
    except (ValueError, TypeError):
        return 0


def _period_end(value: object) -> datetime | None:
    try:
        timestamp = int(value)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp > 0 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _subscription_id(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and value.startswith("sub_") and len(value) <= 255 else None


def _invoice_subscription_id(invoice: dict) -> str | None:
    # Stripe's newer invoice API nests the subscription under parent.
    parent = invoice.get("parent") or {}
    details = parent.get("subscription_details") or {}
    return (_subscription_id(invoice.get("subscription")) or
            _subscription_id(details.get("subscription")))


def _order_from_metadata(db: TenantSession, value: object) -> ServiceOrder | None:
    """Link only a pre-existing recurring order; never trust customer names/emails."""
    try:
        order_id = uuid.UUID(str((value or {}).get("order_id")))
    except (ValueError, AttributeError, TypeError):
        return None
    order = db.get(ServiceOrder, order_id)
    if order is None or order.offer_snapshot.get("billing") != "monthly":
        return None
    return order


def _subscription(db: TenantSession, stripe_id: str, *, order: ServiceOrder | None = None,
                  customer: str | None = None) -> BillingSubscription | None:
    """Associate a verified Stripe ID with at most one internal order/customer."""
    row = db.scalar(select(BillingSubscription).where(
        BillingSubscription.stripe_subscription_id == stripe_id).with_for_update())
    if row is not None and order is not None and row.order_id not in (None, order.id):
        return None
    if row is not None and customer and row.stripe_customer_id not in (None, customer):
        return None
    if row is None:
        row = BillingSubscription(stripe_subscription_id=stripe_id)
        db.add(row)
        db.flush()
    if order is not None:
        row.order_id = order.id
        row.org_id = order.org_id
    if customer:
        row.stripe_customer_id = customer
    return row


def _effective_entitlement(db: TenantSession, row: BillingSubscription,
                           at: datetime | None = None) -> bool:
    """Compute access at query time; an old active boolean can expire overnight."""
    order = db.get(ServiceOrder, row.order_id) if row.order_id else None
    current = at or now()
    paid = bool(order and order.paid_at and order.status in ("paid", "provisioned"))
    if not paid or not row.org_id or not row.period_end:
        return False  # no indefinite access when a period cannot be verified
    in_grace = bool(row.invoice_status == "failed" and row.grace_until and
                    row.grace_until > current)
    if row.status == "canceled" or row.invoice_status == "refunded":
        return False
    if row.invoice_status == "failed":
        return bool(in_grace and row.status in ("active", "trialing", "past_due"))
    return bool(row.status in ("active", "trialing") and row.period_end > current)


def active_subscription_for(db: TenantSession, org_id: uuid.UUID,
                            offer_code: str, at: datetime | None = None) -> bool:
    """Internal billing entitlement helper: callers MUST authorize org_id first.

    Do not authorize a workspace merely from Organization.plan or the cached
    org.settings subscriptions state, which can expire between webhook deliveries.
    """
    rows = db.scalars(select(BillingSubscription).join(
        ServiceOrder, BillingSubscription.order_id == ServiceOrder.id).where(
        BillingSubscription.org_id == org_id, ServiceOrder.offer_code == offer_code)).all()
    return any(_effective_entitlement(db, row, at) for row in rows)


def _reconcile_subscription(db: TenantSession, row: BillingSubscription) -> None:
    """Project verified Stripe subscription facts to the linked organization.

    The persisted snapshot is for owner display only. Access guards must always
    evaluate _effective_entitlement at request time, including after grace expiry.
    """
    order = db.get(ServiceOrder, row.order_id) if row.order_id else None
    if order and order.org_id and not row.org_id:
        row.org_id = order.org_id
    current = now()
    row.entitlement_active = _effective_entitlement(db, row, current)
    row.updated_at = current
    if row.org_id is None or order is None:
        return
    previous_org = db.org_id
    db.set_context(row.org_id, None)
    org = db.get(Organization, row.org_id)
    if org is not None:
        state = dict(org.settings or {})
        plans = dict(state.get("subscriptions") or {})
        plans[order.offer_code] = {
            "status": row.status, "invoice_status": row.invoice_status,
            "access_active": bool(row.entitlement_active),
            "grace_until": row.grace_until.isoformat() if row.grace_until else None,
            "period_end": row.period_end.isoformat() if row.period_end else None,
            "cancel_at_period_end": bool(row.cancel_at_period_end),
        }
        state["subscriptions"] = plans
        org.settings = state
    db.set_context(previous_org, None)


def _checkout_total(obj: dict, expected: int) -> tuple[int, int, int] | None:
    """Compare Stripe's PRE-discount subtotal to the approved list price.

    A coupon changes amount_total, not amount_subtotal. Reject ambiguous/negative
    breakdowns rather than granting access to a cheaper or tampered purchase.
    """
    try:
        total = int(obj.get("amount_total")) if obj.get("amount_total") is not None else expected
        if total < 0:
            return None
        subtotal = obj.get("amount_subtotal")
        if subtotal is None:
            return (total, 0, 0) if total == expected else None  # legacy unsigned mock shape
        if int(subtotal) != expected:
            return None
        details = obj.get("total_details") or {}
        discount = int(details.get("amount_discount") or 0)
        tax = int(details.get("amount_tax") or 0)
        if discount < 0 or tax < 0 or discount > expected:
            return None
        if total != expected - discount + tax:
            return None
        return total, discount, tax
    except (ValueError, TypeError, AttributeError):
        return None


def _process_checkout(db: TenantSession, event: dict, obj: dict) -> str:
    if obj.get("payment_status") not in ("paid", "no_payment_required"):
        return "unpaid"
    order = db.scalar(select(ServiceOrder).where(
        ServiceOrder.provider_ref == obj.get("id")).with_for_update())
    if order is None:
        return "unknown_order"
    breakdown = _checkout_total(obj, order.amount_cents)
    if breakdown is None:
        return "amount_mismatch"
    settled, discount, tax = breakdown
    if obj.get("payment_status") == "no_payment_required" and settled != 0:
        return "amount_mismatch"
    if obj.get("currency") and obj["currency"].lower() != order.currency:
        return "currency_mismatch"
    recurring = order.offer_snapshot.get("billing") == "monthly"
    expected_mode = "subscription" if recurring else "payment"
    if obj.get("mode") and obj["mode"] != expected_mode:
        return "mode_mismatch"
    customer = obj.get("customer")
    customer_id = customer if isinstance(customer, str) and customer.startswith("cus_") else None
    row = None
    if recurring:
        sid = _subscription_id(obj.get("subscription"))
        if not sid:
            return "missing_subscription"
        row = _subscription(db, sid, order=order, customer=customer_id)
        if row is None:
            return "subscription_conflict"
    order.settled_amount_cents = settled
    order.discount_amount_cents = discount
    order.tax_amount_cents = tax
    mark_paid(db, order, provider="stripe", evidence="stripe:" + event["id"])
    if row:
        # If a subscription.updated arrived first, don't overwrite its status.
        # Otherwise remain pending until an authoritative subscription webhook.
        _reconcile_subscription(db, row)
    return "paid"


def _process_subscription(db: TenantSession, event: dict, obj: dict) -> str:
    sid = _subscription_id(obj.get("id"))
    if not sid:
        return "unknown_subscription"
    order = _order_from_metadata(db, obj.get("metadata"))
    cust = obj.get("customer")
    customer = cust if isinstance(cust, str) and cust.startswith("cus_") else None
    row = _subscription(db, sid, order=order, customer=customer)
    if row is None:
        return "subscription_conflict"
    timestamp = _event_time(event)
    if timestamp < row.last_subscription_event_created:
        return "stale_subscription_event"
    status = "canceled" if event["type"] == "customer.subscription.deleted" else obj.get("status")
    if status not in _SUBSCRIPTION_STATES:
        return "unknown_subscription_status"
    if row.status == "canceled" and status != "canceled":
        return "terminal_subscription_canceled"
    row.last_subscription_event_created = timestamp
    row.status = status
    row.cancel_at_period_end = bool(obj.get("cancel_at_period_end", False))
    period = _period_end(obj.get("current_period_end"))
    if period is None:
        items = obj.get("items") or {}
        lines = items.get("data") or [] if isinstance(items, dict) else []
        if lines and isinstance(lines[0], dict):
            period = _period_end(lines[0].get("current_period_end"))
    if period:
        row.period_end = period
    _reconcile_subscription(db, row)
    return "subscription_updated"


def _process_invoice(db: TenantSession, event: dict, obj: dict) -> str:
    sid = _invoice_subscription_id(obj)
    if sid is None:
        return "unmatched_invoice"
    parent = obj.get("parent") or {}
    detail = parent.get("subscription_details") or {}
    order = _order_from_metadata(db, obj.get("metadata")) or _order_from_metadata(
        db, detail.get("metadata"))
    cust = obj.get("customer")
    customer = cust if isinstance(cust, str) and cust.startswith("cus_") else None
    row = _subscription(db, sid, order=order, customer=customer)
    if row is None:
        return "subscription_conflict"
    timestamp = _event_time(event)
    if timestamp < row.last_invoice_event_created:
        return "stale_invoice_event"
    if (timestamp == row.last_invoice_event_created and
            row.invoice_status == "paid" and event["type"] == "invoice.payment_failed"):
        return "stale_invoice_event"
    row.last_invoice_event_created = timestamp
    row.latest_invoice_id = str(obj.get("id") or "")[:255] or row.latest_invoice_id
    if event["type"] == "invoice.paid":
        row.invoice_status = "paid"
        row.grace_until = None
    else:
        row.invoice_status = "failed"
        row.grace_until = now() + timedelta(hours=72)
    _reconcile_subscription(db, row)
    return "invoice_paid" if event["type"] == "invoice.paid" else "invoice_failed"


def _process_refund(db: TenantSession, event: dict, obj: dict) -> str:
    # Stripe charge refunds need an explicit invoice link before they can affect
    # access. A payment_intent alone must never be guessed into a subscription.
    invoice_id = obj.get("invoice")
    if not isinstance(invoice_id, str) or not invoice_id.startswith("in_"):
        return "unmatched_refund"
    row = db.scalar(select(BillingSubscription).where(
        BillingSubscription.latest_invoice_id == invoice_id).with_for_update())
    if row is None:
        return "unmatched_refund"
    if _event_time(event) < row.last_invoice_event_created:
        return "stale_refund_event"
    row.last_invoice_event_created = _event_time(event)
    row.invoice_status = "refunded"
    row.grace_until = None
    _reconcile_subscription(db, row)
    return "refunded"


def handle_stripe_event(db: TenantSession, event: dict) -> str:
    """Apply a signature-verified Stripe event exactly once.

    Provider-ref checkout binding is required before a subscription can grant
    entitlement, even when subscription/invoice events arrive out of order.
    """
    if db.scalar(select(PaymentEvent.id).where(PaymentEvent.provider == "stripe",
                                               PaymentEvent.event_id == event["id"])):
        return "duplicate"
    db.add(PaymentEvent(provider="stripe", event_id=event["id"], event_type=event["type"][:80]))
    obj = (event.get("data") or {}).get("object") or {}
    if not isinstance(obj, dict):
        return "malformed_object"
    kind = event["type"]
    if kind == "checkout.session.completed":
        return _process_checkout(db, event, obj)
    if kind in ("customer.subscription.created", "customer.subscription.updated",
                "customer.subscription.deleted"):
        return _process_subscription(db, event, obj)
    if kind in ("invoice.paid", "invoice.payment_failed"):
        return _process_invoice(db, event, obj)
    if kind in ("charge.refunded",):
        return _process_refund(db, event, obj)
    return "ignored"


def provision(db: TenantSession, order: ServiceOrder, *, operator_id: uuid.UUID | None = None,
              unpaid_reason: str = "") -> Organization:
    """Create the customer's organization and invite its owner. Idempotent per order."""
    if order.org_id is not None:
        # Existing-customer add-ons already identify their workspace. Do not
        # create a second organization or resend an owner invite.
        if order.status == "paid" and order.provisioned_at is None:
            order.status, order.provisioned_at = "provisioned", now()
        db.set_context(order.org_id, None)
        return db.get(Organization, order.org_id)
    if order.status == "paid":
        paid = True
    elif order.status in ("consult_requested", "approved_unpaid") and operator_id and len(unpaid_reason.strip()) >= 4:
        paid = False  # explicit, attributed exception (free consult or pilot) recorded below
        order.payment_evidence = ("NOT PAID. Provisioned by operator: " + unpaid_reason.strip())[:255]
    else:
        raise OrderError("This order has no verified payment. An operator must record a payment "
                         "or give a reason for provisioning without one.")
    assessment = db.get(Assessment, order.assessment_id)
    billing_info = {
        "order_id": str(order.id), "paid": paid,
        "amount_cents": (order.settled_amount_cents if order.settled_amount_cents is not None
                         else order.amount_cents) if paid else 0,
        "offer": order.offer_snapshot.get("name", ""),
    }
    if paid and (order.discount_amount_cents or order.tax_amount_cents):
        billing_info["discount_cents"] = order.discount_amount_cents or 0
        billing_info["tax_cents"] = order.tax_amount_cents or 0
    org = Organization(id=uuid.uuid4(), name=assessment.business_name, industry=assessment.industry,
                       plan=order.offer_code, settings={"billing": billing_info})
    db.set_context(org.id, None)  # RLS: the new row must match the active tenant
    db.add(org)
    db.flush()
    db.add(AssessmentReport(org_id=org.id, excited_text=assessment.excited_text,
                            blocker_text=assessment.blocker_text, result=assessment.result))
    for item in improvements_from(assessment.result):
        db.add(Improvement(org_id=org.id, **item))
    order.org_id, order.status, order.provisioned_at = org.id, "provisioned", now()
    assessment.status = "customer"
    audit(db, org.id, operator_id, "org.provisioned", "organization", org.id,
          {"order_id": str(order.id), "paid": paid})
    for subscription in db.scalars(select(BillingSubscription).where(
            BillingSubscription.order_id == order.id)).all():
        subscription.org_id = org.id
        _reconcile_subscription(db, subscription)
    invite_member(db, org, assessment.email, "owner", invited_by=operator_id)
    return org


def invite_member(db: TenantSession, org: Organization, email: str, role: str,
                  invited_by: uuid.UUID | None) -> str:
    settings = get_settings()
    email = normalize_email(email)
    token = issue_token(db, purpose="invite", email=email, org_id=org.id, role=role,
                        ttl=timedelta(hours=settings.invite_ttl_hours))
    link = f"{settings.public_base_url}/accept-invite?token={token}"
    message = queue_email(
        db, kind="invite", to=email, org_id=org.id,
        subject=f"Your {settings.brand_name} workspace for {org.name}",
        body=(f"You've been invited to the {org.name} workspace on {settings.brand_name} as {role}.\n\n"
              f"Set up your sign-in here (link expires in {settings.invite_ttl_hours} hours):\n{link}\n\n"
              "If you weren't expecting this, you can ignore this message."))
    jobs.enqueue(db, "send_email", {"message_id": str(message.id)}, key=f"email:{message.id}")
    audit(db, org.id, invited_by, "team.invited", "user", None, {"role": role})
    return token


def accept_invite(db: TenantSession, token_row, user: User) -> Membership:
    db.set_context(token_row.org_id, user.id)
    existing = db.scalar(select(Membership).where(Membership.org_id == token_row.org_id,
                                                  Membership.user_id == user.id))
    token_row.used_at = now()
    if existing:
        return existing
    membership = Membership(org_id=token_row.org_id, user_id=user.id, role=token_row.role or "readonly")
    db.add(membership)
    audit(db, token_row.org_id, user.id, "team.joined", "user", user.id, {"role": membership.role})
    return membership
