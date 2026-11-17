"""MF-036: owner-consented, durable tenant research budget and reservation ledger.

Only explicitly authorized and signed OpenAI MF-035 planned runs can reserve
forecast spend. No live transport, provider key, dispatcher or trusted receipt.
Every operation requires an authenticated tenant-scoped SQLAlchemy session.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.models import (
    Membership, Organization, ServiceOrder, VisibilityResearchBudget, VisibilityResearchReservation,
)
from app.schemas.ai_visibility_run import ExperimentSpec
from app.services.ai_visibility_adapter import build_ai_visibility_plan
from app.services.openai_visibility_pilot import build_openai_web_search_request

MAX_MONTHLY_MICROS = 500_000_000  # Pilot product maximum USD $500; NOT provider billing cap.


class ResearchNotAuthorized(ValueError):
    """No current owner permission, confirmed budget or eligible subscription."""


class ResearchBudgetExceeded(ValueError):
    """The projected reservation exceeds a consented monthly ceiling."""


class ResearchConflict(ValueError):
    """The requested transition or signed plan is not safe."""


class ResearchAlreadyCancelled(ResearchConflict):
    """An old cancellation cannot be mistaken for a fresh reservation."""


# An approved *offline* synthetic contract, NOT a quoted OpenAI tariff.
# Reject all other provider/model profiles until finance reviews a real
# model-specific tariff, maximum tool use, and independently metered ceiling.
APPROVED_OFFLINE_MODEL_CEILINGS = {
    ("openai_web_search", "synthetic-openai-model"): (250_000, 2),
}


def _month() -> date:
    now = datetime.now(timezone.utc)
    return date(now.year, now.month, 1)


def _micros(amount: Any, *, allow_zero: bool = False) -> int:
    """Convert literal decimal USD into integer micro-dollars without binary floats."""
    if (isinstance(amount, (float, bool)) or
            not isinstance(amount, (str, int, Decimal)) or
            not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,6})?", str(amount))):
        raise ResearchConflict("Research budgets require an exact plain decimal USD amount.")
    try:
        amount_dec = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        raise ResearchConflict("Research budget is not a decimal USD amount.") from None
    if not amount_dec.is_finite():
        raise ResearchConflict("Research budget must be finite.")
    units = amount_dec * Decimal(1_000_000)
    if units != units.to_integral_value():
        raise ResearchConflict("Research budget has unsupported fractional micro-USD.")
    value = int(units)
    if value < 0 or (not allow_zero and value == 0) or value > MAX_MONTHLY_MICROS:
        raise ResearchConflict("Research budget is outside pilot limits.")
    return value


def _current_owner(db, org_id: uuid.UUID, user_id: uuid.UUID) -> Organization:
    if getattr(db, "org_id", None) != org_id:
        raise ResearchNotAuthorized("The current workspace context is not authorized.")
    org = db.get(Organization, org_id)
    if org is None or org.status != "active":
        raise ResearchNotAuthorized("An active eligible workspace subscription is required.")
    billing = (org.settings or {}).get("billing")
    if not isinstance(billing, dict) or billing.get("paid") is not True:
        raise ResearchNotAuthorized("A verified paid workspace subscription is required.")
    try:
        order_id = uuid.UUID(str(billing.get("order_id", "")))
    except (ValueError, TypeError, AttributeError):
        raise ResearchNotAuthorized("Workspace payment evidence is unavailable.") from None
    order = db.get(ServiceOrder, order_id)
    if (order is None or order.org_id != org_id or order.paid_at is None or
            order.amount_cents <= 0 or order.payment_provider in ("none", "") or
            order.offer_code != org.plan or order.status != "provisioned"):
        raise ResearchNotAuthorized("Workspace payment has not been verified.")
    membership = db.scalar(select(Membership).where(
        Membership.org_id == org_id, Membership.user_id == user_id))
    if membership is None or membership.role not in ("owner", "admin"):
        raise ResearchNotAuthorized("Only workspace owners or administrators can approve research.")
    return org


def budget_view(value: VisibilityResearchBudget | None) -> dict[str, Any]:
    if value is None:
        return {"status": "not_authorized", "monthly_ceiling_micros": 0,
                "held_micros": 0, "reconciled_micros": 0,
                "available_micros": 0, "live_dispatch_enabled": False}
    return {
        "status": "owner_authorized_reservations_only",
        "period_month": value.period_month.isoformat(),
        "monthly_ceiling_micros": value.ceiling_micros,
        "held_micros": value.held_micros,
        "reconciled_micros": value.reconciled_micros,
        "available_micros": value.ceiling_micros - value.held_micros - value.reconciled_micros,
        "live_dispatch_enabled": False,
        "note": "These are planned upper estimates, not measured charges or provider billing limits.",
    }


def reservation_view(value: VisibilityResearchReservation) -> dict[str, Any]:
    return {
        "id": str(value.id), "planned_run_id": value.planned_run_id,
        "idempotency_key": value.idempotency_key,
        "plan_signature": value.plan_signature,
        "request_signature": value.request_signature,
        "provider_id": value.provider_id, "model_id": value.model_id,
        "ceiling_micros": value.ceiling_micros,
        "status": value.status,
        "approved_at": value.approved_at.isoformat() if value.approved_at else None,
        "cancelled_at": value.cancelled_at.isoformat() if value.cancelled_at else None,
        "provider_requests_sent": 0,
        "verified_provider_results": 0,
        "live_dispatch_enabled": False,
    }


def get_current_budget(db, org_id: uuid.UUID, user_id: uuid.UUID):
    _current_owner(db, org_id, user_id)
    return db.scalar(select(VisibilityResearchBudget).where(
        VisibilityResearchBudget.org_id == org_id,
        VisibilityResearchBudget.period_month == _month()))


def authorize_budget(db, org_id: uuid.UUID, user_id: uuid.UUID, *,
                     monthly_ceiling_usd: str, owner_confirmed: bool):
    """Owner grants a monthly *reservation* ceiling; no authority to send requests."""
    _current_owner(db, org_id, user_id)
    if owner_confirmed is not True:
        raise ResearchNotAuthorized("Explicit owner budget confirmation is required.")
    ceiling = _micros(monthly_ceiling_usd, allow_zero=True)
    current = db.scalar(select(VisibilityResearchBudget).where(
        VisibilityResearchBudget.org_id == org_id,
        VisibilityResearchBudget.period_month == _month()).with_for_update())
    if current:
        if current.held_micros + current.reconciled_micros > ceiling:
            raise ResearchBudgetExceeded("The lower limit would strand prior reservations.")
        current.ceiling_micros = ceiling
        current.authorized_by = user_id
        current.authorized_at = datetime.now(timezone.utc)
        db.flush()
        return current, False
    budget = VisibilityResearchBudget(
        org_id=org_id, period_month=_month(),
        ceiling_micros=ceiling, held_micros=0, reconciled_micros=0,
        authorized_by=user_id)
    db.add(budget)
    db.flush()
    return budget, True


def reserve_planned_search(db, org_id: uuid.UUID, user_id: uuid.UUID, *,
                           spec: ExperimentSpec, planned_run_id: str,
                           owner_confirmed: bool):
    """Atomically reserve one exact MF-022/MF-035 planned request.

    Requires a previously confirmed monthly budget. A second caller with the
    identical deterministic idempotency key gets the same reservation, never a
    second hold. All rows stay under FORCE RLS. No network can be reached.
    """
    _current_owner(db, org_id, user_id)
    if owner_confirmed is not True:
        raise ResearchNotAuthorized("Approve this specific planned search before reservation.")
    if not isinstance(spec, ExperimentSpec):
        spec = ExperimentSpec.model_validate(spec)
    plan = build_ai_visibility_plan(spec)
    run = next((r for r in plan.requests if r.id == planned_run_id), None)
    if run is None:
        raise ResearchConflict("That run does not belong to the approved experiment.")
    request = build_openai_web_search_request(spec, plan, run)
    # The caller can choose a *higher* forecast ceiling, never set a lower
    # charge floor or enable a provider model without a server-approved policy.
    # This pilot policy is synthetic and authorizes NO actual API request.
    policy = APPROVED_OFFLINE_MODEL_CEILINGS.get((
        run.provider_family.value, request.body["model"]
    ))
    if policy is None or run.search_uses_ceiling > policy[1]:
        raise ResearchConflict("Provider/model pricing has not been approved for research reservations.")
    expected = _micros(run.estimated_cost_ceiling_usd)
    if expected < policy[0]:
        raise ResearchConflict("The proposed per-run ceiling is below the server-approved safety floor.")
    budget = db.scalar(select(VisibilityResearchBudget).where(
        VisibilityResearchBudget.org_id == org_id,
        VisibilityResearchBudget.period_month == _month()).with_for_update())
    if budget is None or budget.ceiling_micros <= 0:
        raise ResearchNotAuthorized("Approve a monthly research budget first.")
    existing = db.scalar(select(VisibilityResearchReservation).where(
        VisibilityResearchReservation.org_id == org_id,
        VisibilityResearchReservation.idempotency_key == request.idempotency_key))
    if existing:
        if existing.status == "cancelled":
            raise ResearchAlreadyCancelled("This search reservation was cancelled; create a new approved plan.")
        if (existing.planned_run_id != request.run_id or
                existing.plan_signature != request.experiment_signature or
                existing.request_signature != request.request_signature or
                existing.ceiling_micros != expected or
                existing.provider_id != request.provider_id or
                existing.model_id != request.body["model"]):
            raise ResearchConflict("Idempotency conflict: the research request was changed.")
        return existing, False
    if budget.held_micros + budget.reconciled_micros + expected > budget.ceiling_micros:
        raise ResearchBudgetExceeded("Research reservation exceeds the confirmed monthly ceiling.")
    row = VisibilityResearchReservation(
        org_id=org_id, budget_id=budget.id, idempotency_key=request.idempotency_key,
        planned_run_id=request.run_id, plan_signature=request.experiment_signature,
        request_signature=request.request_signature, provider_id=request.provider_id,
        model_id=request.body["model"], ceiling_micros=expected,
        approved_by=user_id, status="reserved",
    )
    budget.held_micros += expected
    db.add(row)
    db.flush()
    return row, True


def cancel_reserved_search(db, org_id: uuid.UUID, user_id: uuid.UUID,
                           reservation_id: uuid.UUID):
    """Release only never-dispatched reservations, under the same serialized budget lock.

    Unknown/after-dispatch charges are never released by this owner endpoint.
    """
    _current_owner(db, org_id, user_id)
    # Lock the budget first, matching reserve() ordering for consistent concurrency.
    target = db.scalar(select(VisibilityResearchReservation).where(
        VisibilityResearchReservation.id == reservation_id,
        VisibilityResearchReservation.org_id == org_id))
    if target is None:
        raise ResearchConflict("Research reservation was not found in this workspace.")
    budget = db.scalar(select(VisibilityResearchBudget).where(
        VisibilityResearchBudget.id == target.budget_id,
        VisibilityResearchBudget.org_id == org_id).with_for_update())
    if budget is None:
        raise ResearchConflict("Research budget is unavailable.")
    row = db.scalar(select(VisibilityResearchReservation).where(
        VisibilityResearchReservation.id == reservation_id,
        VisibilityResearchReservation.org_id == org_id).with_for_update(
        ).execution_options(populate_existing=True))
    # A reservation was already loaded to obtain budget_id; always refresh
    # AFTER acquiring the budget lock, or SQLAlchemy's identity map may replay
    # a pre-lock 'reserved' value and release its hold twice.
    if row is None:
        raise ResearchConflict("Research reservation is unavailable.")
    if row.status == "cancelled":
        return row, False
    if row.status != "reserved":
        raise ResearchConflict("A dispatched or unknown-charge request cannot be cancelled.")
    if budget.held_micros < row.ceiling_micros:
        raise ResearchConflict("Budget accounting is inconsistent; no reservation changed.")
    row.status = "cancelled"
    row.cancelled_at = datetime.now(timezone.utc)
    budget.held_micros -= row.ceiling_micros
    db.flush()
    return row, True


def list_reservations(db, org_id: uuid.UUID, user_id: uuid.UUID):
    _current_owner(db, org_id, user_id)
    return db.scalars(select(VisibilityResearchReservation).where(
        VisibilityResearchReservation.org_id == org_id).order_by(
            VisibilityResearchReservation.approved_at.desc(),
            VisibilityResearchReservation.id.desc()).limit(100)).all()
