"""Service orders: customer approval -> verified payment (or recorded exception) -> workspace."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from ..config import get_settings
from ..db import TenantSession
from ..models import (Assessment, AssessmentReport, Improvement, Membership, Organization,
                      PaymentEvent, ServiceOrder, User, now)
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


def handle_stripe_event(db: TenantSession, event: dict) -> str:
    """Apply a signature-verified Stripe event exactly once."""
    if db.scalar(select(PaymentEvent.id).where(PaymentEvent.provider == "stripe",
                                               PaymentEvent.event_id == event["id"])):
        return "duplicate"
    db.add(PaymentEvent(provider="stripe", event_id=event["id"], event_type=event["type"][:80]))
    if event["type"] != "checkout.session.completed":
        return "ignored"
    obj = (event.get("data") or {}).get("object") or {}
    if obj.get("payment_status") not in ("paid", "no_payment_required"):
        return "unpaid"
    order = db.scalar(select(ServiceOrder).where(ServiceOrder.provider_ref == obj.get("id")).with_for_update())
    if order is None:
        return "unknown_order"
    if obj.get("amount_total") is not None and int(obj["amount_total"]) != order.amount_cents:
        return "amount_mismatch"
    mark_paid(db, order, provider="stripe", evidence="stripe:" + event["id"])
    return "paid"


def provision(db: TenantSession, order: ServiceOrder, *, operator_id: uuid.UUID | None = None,
              unpaid_reason: str = "") -> Organization:
    """Create the customer's organization and invite its owner. Idempotent per order."""
    if order.org_id is not None:
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
    org = Organization(id=uuid.uuid4(), name=assessment.business_name, industry=assessment.industry,
                       plan=order.offer_code,
                       settings={"billing": {"order_id": str(order.id), "paid": paid,
                                             "amount_cents": order.amount_cents if paid else 0,
                                             "offer": order.offer_snapshot.get("name", "")}})
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
