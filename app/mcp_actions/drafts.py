"""MCP write tools: owner-controlled tenant draft proposals, never direct sends.

Only callable from the OAuth-verified MCP boundary. Source of authority is
the validated signed bearer linked to the MeasuredField user's immutable OIDC
subject, followed by a current enabled-user and tenant membership check under
PostgreSQL FORCE RLS. Model-supplied UUIDs only select objects; cannot authorize.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import session_scope
from app.mcp_connector.gateway import ConnectorAccessDenied
from app.mcp_plugin.links import linked_subject_user_id
from app.models import Lead, McpActionDraft, Membership, Organization, User
from app.security import can, rate_limited
from app.services.audit import audit

_REQUEST = re.compile(r"[A-Za-z0-9_-]{12,80}")  # checked with fullmatch
# Reject invisible display controls and bidi overrides in model-authored drafts.
# Ordinary non-ASCII letters (including Spanish accents) remain allowed.
_HIDDEN = frozenset(chr(c) for a, b in (
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F)) for c in range(a, b + 1))
_HIDDEN = _HIDDEN | {"\u061c", "\u034f", "\u180e", "\u00ad", "\ufeff"}


def _id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        raise ConnectorAccessDenied("Business or inquiry is unavailable") from None


def _text(message: str) -> str:
    if not isinstance(message, str):
        raise ValueError("A draft message is required")
    body = message.strip()
    if not 1 <= len(body) <= 4000 or "\x00" in body:
        raise ValueError("Reply drafts must contain 1 to 4000 characters")
    if any(ch in _HIDDEN for ch in body):
        raise ValueError("Reply draft contains invisible or bidirectional formatting controls")
    return body


class OwnerDraftGateway:
    """Save inert reply drafts; no app, email, publishing or shell side effects."""

    def __init__(self, *, issuer: str, audience: str, read_scope: str,
                 write_scope: str) -> None:
        if not all(isinstance(v, str) and v.strip() for v in
                   (issuer, audience, read_scope, write_scope)):
            raise ValueError("A configured signed-token issuer, audience and scopes are required")
        self.issuer, self.audience = issuer, audience
        self.read_scope, self.write_scope = read_scope, write_scope

    def _actor(self, bearer):
        identity = getattr(bearer, "identity", None)
        if (identity is None or identity.issuer != self.issuer
                or identity.audience != self.audience
                or not isinstance(identity.scopes, frozenset)
                or not {self.read_scope, self.write_scope}.issubset(identity.scopes)
                or not isinstance(bearer.issued_at, int)
                or not isinstance(identity.subject, str)):
            raise ConnectorAccessDenied("Write permission is unavailable")
        # A user can immediately revoke the immutable OIDC account link.
        # Never convert a claim-supplied user UUID directly into authorization.
        user_id = linked_subject_user_id(identity.issuer, identity.subject,
                                          bearer.issued_at)
        if not isinstance(user_id, uuid.UUID):
            raise ConnectorAccessDenied("The owner connection is no longer authorized")
        return user_id

    def save_reply_draft(self, bearer, business_id: str, inquiry_id: str,
                         message: str, request_id: str) -> dict:
        """Idempotent within the verified OAuth user and selected business."""
        user_id = self._actor(bearer)
        org_id, lead_id = _id(business_id), _id(inquiry_id)
        body = _text(message)
        if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
            raise ValueError("A stable 12-to-80-character request identifier is required")
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        client_id = getattr(bearer, "client_id", "")
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 250:
            raise ConnectorAccessDenied("OAuth client identity is unavailable")

        def operation() -> dict:
            with session_scope(user_id=user_id) as db:
                actor = db.get(User, user_id)
                if actor is None or actor.disabled_at is not None:
                    raise ConnectorAccessDenied("The connected account is disabled")
                # FORCE RLS permits an owner to SELECT their membership without
                # an active tenant, but UPDATE row-locks require tenant context.
                preflight = db.scalar(select(Membership).where(
                    Membership.user_id == user_id, Membership.org_id == org_id))
                if preflight is None:
                    raise ConnectorAccessDenied("This business does not allow draft changes")
                db.set_context(org_id, user_id)
                member = db.scalar(select(Membership).where(
                    Membership.user_id == user_id,
                    Membership.org_id == org_id).with_for_update())
                if member is None or member.role not in ("owner", "admin") or not can(
                        member.role, "leads.write"):
                    raise ConnectorAccessDenied("This business does not allow draft changes")
                org = db.get(Organization, org_id)
                lead = db.get(Lead, lead_id)
                if (org is None or org.status != "active"
                        or lead is None or lead.org_id != org_id):
                    raise ConnectorAccessDenied("Business or inquiry is unavailable")
                existing = db.scalar(select(McpActionDraft).where(
                    McpActionDraft.org_id == org_id,
                    McpActionDraft.created_by == user_id,
                    McpActionDraft.idempotency_key == request_id))
                if existing is not None:
                    if (existing.lead_id != lead_id or existing.body_hash != content_hash
                            or existing.oauth_client_id != client_id):
                        raise ConnectorAccessDenied("Request identifier has already been used")
                    return self._outcome(existing, already_exists=True)
                if rate_limited(db, "mcp-reply-draft:" + str(user_id) + ":" + str(org_id),
                                limit=30, window_seconds=3600):
                    raise ConnectorAccessDenied("Draft creation rate limit exceeded")
                entry = McpActionDraft(
                    org_id=org_id, lead_id=lead_id, created_by=user_id,
                    oauth_client_id=client_id, kind="inquiry_reply", body=body,
                    body_hash=content_hash, idempotency_key=request_id,
                    status="pending_review")
                db.add(entry)
                db.flush()
                audit(db, org_id, user_id, "mcp.reply_draft_created",
                      "mcp_action_draft", entry.id, {"kind": "inquiry_reply"})
                return self._outcome(entry, already_exists=False)

        try:
            return operation()
        except IntegrityError:
            # Concurrent equal-key requests are serialized by PostgreSQL UNIQUE.
            # Recheck the completed row; never reinterpret a collision as permission.
            with session_scope(user_id=user_id) as db:
                member = db.scalar(select(Membership).where(
                    Membership.user_id == user_id, Membership.org_id == org_id))
                if (member is None or member.role not in ("owner", "admin")
                        or not can(member.role, "leads.write")):
                    raise ConnectorAccessDenied("Draft write is unavailable") from None
                db.set_context(org_id, user_id)
                row = db.scalar(select(McpActionDraft).where(
                    McpActionDraft.created_by == user_id,
                    McpActionDraft.idempotency_key == request_id))
                if (row is None or row.lead_id != lead_id
                        or row.body_hash != content_hash
                        or row.oauth_client_id != client_id):
                    raise ConnectorAccessDenied("Draft write conflict") from None
                return self._outcome(row, already_exists=True)

    @staticmethod
    def _outcome(row: McpActionDraft, *, already_exists: bool) -> dict:
        return {
            "draft_id": str(row.id), "business_id": str(row.org_id),
            "inquiry_id": str(row.lead_id), "status": row.status,
            "already_exists": already_exists,
            "customer_contacted": False, "published": False,
            "owner_review_required": True,
            "review_url": get_settings().public_base_url.rstrip("/") + "/app/mcp/approvals",
        }
