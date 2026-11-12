"""Read-only, tenant-aware data gateway for a future authenticated ChatGPT MCP plugin.

The caller MUST construct VerifiedOAuthIdentity only after verifying an OAuth access
token's signature, issuer, resource/audience, expiry, scope and revocation. This module
does not validate bearer tokens or accept identities from MCP tool parameters.
No production subject resolver is installed; an unmapped identity always fails closed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select

from app.db import session_scope
from app.models import Lead, Membership, Organization, Site, User
from app.security import can


class ConnectorAccessDenied(PermissionError):
    """No information about other businesses or identities is disclosed."""


@dataclass(frozen=True)
class VerifiedOAuthIdentity:
    """Created ONLY inside an already-verified OAuth resource-server boundary."""

    issuer: str
    subject: str
    audience: str
    scopes: frozenset[str]


# A durable (issuer, sub) -> internal User.id mapping is still required.
SubjectResolver = Callable[[str, str], uuid.UUID | None]


def unconfigured_subject_resolver(issuer: str, subject: str) -> None:
    """Production-safe default: no external identity silently becomes a user."""
    return None


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ConnectorAccessDenied("Business or inquiry is unavailable") from None


class ReadOnlyBusinessGateway:
    """SQLAlchemy/forced-RLS adapter with membership checked on every invocation."""

    def __init__(
        self,
        *,
        issuer: str,
        resource_audience: str,
        resolve_subject: SubjectResolver = unconfigured_subject_resolver,
    ):
        if not issuer or not resource_audience:
            raise ValueError("Trusted OAuth issuer and resource audience are required")
        self.issuer = issuer
        self.resource_audience = resource_audience
        self.resolve_subject = resolve_subject

    def _user(self, identity: VerifiedOAuthIdentity) -> uuid.UUID:
        if not isinstance(identity, VerifiedOAuthIdentity):
            raise ConnectorAccessDenied("OAuth resource-server identity is required")
        if (
            identity.issuer != self.issuer
            or identity.audience != self.resource_audience
            or "mf:read" not in identity.scopes
            or not identity.subject
        ):
            raise ConnectorAccessDenied("OAuth identity is not authorized")
        user_id = self.resolve_subject(identity.issuer, identity.subject)
        if not isinstance(user_id, uuid.UUID):
            raise ConnectorAccessDenied(
                "OAuth identity has no linked MeasuredField account"
            )
        return user_id

    @staticmethod
    def _active_user(db, user_id: uuid.UUID) -> None:
        user = db.get(User, user_id)
        if user is None or user.disabled_at is not None:
            raise ConnectorAccessDenied("Account is unavailable")

    @staticmethod
    def _enter_business(db, user_id: uuid.UUID, org_id: uuid.UUID) -> Organization:
        # The initial context sees only this user's membership rows under FORCE RLS.
        membership = db.scalar(
            select(Membership).where(
                Membership.org_id == org_id, Membership.user_id == user_id
            )
        )
        if membership is None or not can(membership.role, "read"):
            raise ConnectorAccessDenied("Business is unavailable")
        db.set_context(org_id, user_id)
        org = db.get(Organization, org_id)
        if org is None or org.status != "active":
            raise ConnectorAccessDenied("Business is unavailable")
        return org

    def businesses_for(self, identity: VerifiedOAuthIdentity) -> list[dict]:
        user_id = self._user(identity)
        with session_scope(user_id=user_id) as db:
            self._active_user(db, user_id)
            memberships = db.scalars(
                select(Membership)
                .where(Membership.user_id == user_id)
                .order_by(Membership.created_at)
            ).all()
            result: list[dict] = []
            for item in memberships:
                if not can(item.role, "read"):
                    continue
                org = db.get(Organization, item.org_id)
                if org is not None and org.status == "active":
                    result.append(
                        {"id": str(org.id), "name": org.name, "role": item.role}
                    )
            return result[:200]

    def overview_for(self, identity: VerifiedOAuthIdentity, business_id: str) -> dict:
        user_id, org_id = self._user(identity), _id(business_id)
        with session_scope(user_id=user_id) as db:
            self._active_user(db, user_id)
            org = self._enter_business(db, user_id, org_id)
            total = db.scalar(
                select(func.count()).select_from(Lead).where(Lead.org_id == org_id)
            )
            new = db.scalar(
                select(func.count())
                .select_from(Lead)
                .where(Lead.org_id == org_id, Lead.status == "new")
            )
            site_count = db.scalar(
                select(func.count()).select_from(Site).where(Site.org_id == org_id)
            )
            return {
                "business_id": str(org.id),
                "business_name": org.name,
                "inquiries_total": total or 0,
                "inquiries_new": new or 0,
                "websites_total": site_count or 0,
                "evidence": "measuredfield_first_party_records",
                "search_rankings": None,
                "revenue_attributed": None,
            }

    def inquiries_for(
        self, identity: VerifiedOAuthIdentity, business_id: str, limit: int = 20
    ) -> list[dict]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise ValueError("Inquiry limit must be an integer from 1 through 50")
        user_id, org_id = self._user(identity), _id(business_id)
        with session_scope(user_id=user_id) as db:
            self._active_user(db, user_id)
            self._enter_business(db, user_id, org_id)
            rows = db.scalars(
                select(Lead)
                .where(Lead.org_id == org_id)
                .order_by(Lead.created_at.desc(), Lead.id.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": str(lead.id),
                    "name": lead.name,
                    "service": lead.service,
                    "status": lead.status,
                    "created_at": lead.created_at.isoformat(),
                    "follow_up_on": lead.follow_up_on.isoformat()
                    if lead.follow_up_on is not None
                    else None,
                }
                for lead in rows
            ]

    def inquiry_for(
        self, identity: VerifiedOAuthIdentity, business_id: str, inquiry_id: str
    ) -> dict:
        user_id, org_id, lead_id = (
            self._user(identity),
            _id(business_id),
            _id(inquiry_id),
        )
        with session_scope(user_id=user_id) as db:
            self._active_user(db, user_id)
            self._enter_business(db, user_id, org_id)
            lead = db.get(Lead, lead_id)
            if lead is None or lead.org_id != org_id:
                raise ConnectorAccessDenied("Inquiry is unavailable")
            return {
                "id": str(lead.id),
                "business_id": str(org_id),
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "service": lead.service,
                "timing": lead.timing,
                "message": lead.message,
                "status": lead.status,
                "created_at": lead.created_at.isoformat(),
                "follow_up_on": lead.follow_up_on.isoformat()
                if lead.follow_up_on is not None
                else None,
                "evidence": "measuredfield_first_party_inquiry_record",
            }
