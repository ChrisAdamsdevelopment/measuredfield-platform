"""Tenant audit trail. Metadata must never contain message bodies or contact details."""
from __future__ import annotations

import uuid

from ..models import AuditEvent


def audit(db, org_id: uuid.UUID, actor_user_id: uuid.UUID | None, action: str,
          target_type: str = "", target_id=None, meta: dict | None = None) -> None:
    db.add(AuditEvent(org_id=org_id, actor_user_id=actor_user_id, action=action,
                      target_type=target_type, target_id=str(target_id) if target_id else "",
                      meta=meta or {}))
