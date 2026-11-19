"""Minimal platform-operator security history, no PII/metadata free-text.

For successful operator actions, write into the same transaction as the
authorized change, so an eventual rollback removes both. For business-rule
denials whose HTTP error causes middleware rollback, use an independent short
transaction to persist the denial without accidentally committing other work.
Authorization and CSRF rejections in dependencies are NOT handled here.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from ..db import session_scope
from ..models import PlatformSecurityEvent

ACTIONS = frozenset((
    "order.mark_paid", "order.provision", "jobs.run",
    "domain.verify", "review.decide", "skill.rollback",
))
TARGETS = frozenset((
    "order", "job_queue", "domain", "lesson_review", "shared_skill",
))
REASONS = frozenset((
    "", "invalid_reference", "not_found", "invalid_state", "already_paid",
    "not_provisionable", "invalid_decision", "consent_withdrawn",
    "already_verified", "no_active_version", "operation_failed",
))


def parse_target_id(raw) -> uuid.UUID | None:
    """Only UUID identifiers are eligible; domains, strings and tokens are not."""
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def record_operator_action(
    db, *, actor_user_id: uuid.UUID, action: str, outcome: str,
    target_type: str, target_id=None, reason_code: str = "",
) -> PlatformSecurityEvent:
    """Write a prevalidated security event with no arbitrary metadata."""
    if not isinstance(actor_user_id, uuid.UUID):
        raise ValueError("An authenticated operator actor UUID is required")
    if action not in ACTIONS or outcome not in ("approved", "denied"):
        raise ValueError("Unknown operator action or outcome")
    if target_type not in TARGETS or reason_code not in REASONS:
        raise ValueError("Unknown target or reason code")
    if outcome == "approved" and reason_code:
        raise ValueError("Approved actions cannot have a denial code")
    if outcome == "denied" and not reason_code:
        raise ValueError("Denied actions require a safe reason code")
    parsed = parse_target_id(target_id)
    if target_id is not None and parsed is None:
        raise ValueError("Only UUID target IDs may be recorded")
    event = PlatformSecurityEvent(
        actor_user_id=actor_user_id, action=action,
        outcome=outcome, target_type=target_type,
        target_id=parsed, reason_code=reason_code,
    )
    db.add(event)
    db.flush()
    return event


def record_operator_denial(
    *, actor_user_id: uuid.UUID, action: str, target_type: str,
    target_id=None, reason_code: str,
) -> None:
    """Durably record an authenticated operator's rejected action.

    Do not use the rejected business operation's transaction: 4xx/5xx
    responses roll it back, so the security event would otherwise disappear.
    """
    with session_scope(user_id=actor_user_id) as db:
        record_operator_action(
            db, actor_user_id=actor_user_id, action=action, outcome="denied",
            target_type=target_type, target_id=target_id,
            reason_code=reason_code,
        )


def recent_security_events(db, *, limit: int = 50) -> list[PlatformSecurityEvent]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")
    return list(db.scalars(
        select(PlatformSecurityEvent)
        .order_by(PlatformSecurityEvent.created_at.desc(),
                  PlatformSecurityEvent.id.desc()).limit(limit)
    ))
