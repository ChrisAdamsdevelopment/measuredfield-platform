"""Platform identity lookup. No model-callable identity-link creation API."""
from __future__ import annotations

import uuid
from sqlalchemy import select

from app.db import new_session
from app.models import McpIdentityLink, User


def linked_subject_user_id(issuer: str, subject: str,
                           issued_at: int | None = None) -> uuid.UUID | None:
    """Use only a previously verified, still active owner identity binding."""
    if (not isinstance(issuer, str) or not isinstance(subject, str)
            or not issuer or not subject or len(issuer) > 250 or len(subject) > 250
            or (issued_at is not None and
                (type(issued_at) is not int or issued_at < 1))):
        return None
    db = new_session()
    try:
        db.connection(execution_options={"postgresql_readonly": True})
        link = db.scalar(select(McpIdentityLink).where(
            McpIdentityLink.issuer == issuer,
            McpIdentityLink.subject == subject,
            McpIdentityLink.revoked_at.is_(None)))
        if link is None or link.verified_method != "oidc_pkce_owner":
            return None
        if issued_at is not None and issued_at < int(link.linked_at.timestamp()):
            return None
        user = db.get(User, link.user_id)
        return user.id if user is not None and user.disabled_at is None else None
    finally:
        db.rollback()
        db.close()
