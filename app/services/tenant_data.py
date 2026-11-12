"""Tenant data export, deletion and retention.

Deletion is honest about its limits: it removes the organization's rows from the live
database and returns a receipt with per-table counts, and it states that copies inside
the database provider's backups persist until the backup retention window passes.
"""
from __future__ import annotations

import base64
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select, text

from ..config import get_settings
from ..db import TenantSession
from ..models import (TENANT_MODELS, TENANT_TABLES, AuthToken, DataRequest, DeletionReceipt,
                      EmailMessage, LessonReview, Organization, Session, SharedSkill, SharedSkillVersion,
                      SiteHost, now)
from . import jobs
from .audit import audit

EXPORT_VERSION = 1


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def export_org(db: TenantSession, org_id: uuid.UUID, *, include_files: bool = True) -> dict:
    """Everything the platform holds for one organization, as plain JSON-able data."""
    org = db.get(Organization, org_id)
    bundle: dict = {
        "export_version": EXPORT_VERSION, "generated_at": now().isoformat(),
        "organization": {c.name: _plain(getattr(org, c.name)) for c in Organization.__table__.columns},
        "tables": {},
    }
    for table in reversed(TENANT_TABLES):
        model = TENANT_MODELS[table]
        columns = [c for c in model.__table__.columns if include_files or c.name != "data"]
        rows = db.execute(select(*columns).where(model.org_id == org_id)).mappings().all()
        bundle["tables"][table] = [{k: _plain(v) for k, v in row.items()} for row in rows]
    bundle["site_hosts"] = [
        {"hostname": h.hostname, "kind": h.kind, "verified": h.verified_at is not None}
        for h in db.scalars(select(SiteHost).where(SiteHost.org_id == org_id))]
    return bundle


def request_deletion(db: TenantSession, org: Organization, user_id: uuid.UUID) -> DataRequest:
    settings = get_settings()
    run_after = now() + timedelta(hours=settings.deletion_grace_hours)
    request = DataRequest(org_id=org.id, kind="delete", requested_by=user_id, run_after=run_after)
    db.add(request)
    db.flush()
    org.status = "deletion_pending"
    audit(db, org.id, user_id, "data.deletion_requested", "organization", org.id,
          {"run_after": run_after.isoformat()})
    jobs.enqueue(db, "delete_org", {"request_id": str(request.id)}, key=f"delete_org:{request.id}",
                 org_id=org.id, run_after=run_after)
    return request


def cancel_deletion(db: TenantSession, org: Organization, user_id: uuid.UUID) -> bool:
    pending = db.scalars(select(DataRequest).where(DataRequest.kind == "delete",
                                                   DataRequest.status == "requested")).all()
    if not pending:
        return False
    for request in pending:
        request.status = "cancelled"
    org.status = "active"
    audit(db, org.id, user_id, "data.deletion_cancelled", "organization", org.id)
    return True


def delete_org_now(db: TenantSession, org_id: uuid.UUID) -> dict | None:
    """Erase one organization. Runs inside that organization's tenant context."""
    org = db.get(Organization, org_id)
    if org is None:
        return None
    if not db.scalar(select(func.count()).select_from(DataRequest).where(
            DataRequest.kind == "delete", DataRequest.status == "requested")):
        return None  # cancelled during the grace period
    counts: dict[str, int] = {}
    # Shared-library provenance first: anything published from this org is revoked.
    counts["shared_skill_versions_revoked"] = revoke_org_contributions(db, org_id)
    counts["lesson_reviews"] = db.execute(delete(LessonReview).where(LessonReview.source_org_id == org_id)).rowcount
    for table in TENANT_TABLES:  # fixed list of identifiers, never user input
        counts[table] = db.execute(text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": org_id}).rowcount  # noqa: S608
    counts["site_hosts"] = db.execute(delete(SiteHost).where(SiteHost.org_id == org_id)).rowcount
    counts["email_outbox"] = db.execute(delete(EmailMessage).where(EmailMessage.org_id == org_id)).rowcount
    counts["auth_tokens"] = db.execute(delete(AuthToken).where(AuthToken.org_id == org_id)).rowcount
    db.execute(text("UPDATE sessions SET active_org_id = NULL WHERE active_org_id = :o"), {"o": org_id})
    db.execute(text("UPDATE service_orders SET org_id = NULL WHERE org_id = :o"), {"o": org_id})
    counts["organizations"] = db.execute(delete(Organization).where(Organization.id == org_id)).rowcount
    receipt = {
        "org_id": str(org_id), "completed_at": now().isoformat(), "deleted_rows": counts,
        "live_database": "DELETED",
        "provider_backups": "PENDING_EXTERNAL_PURGE: copies remain in managed database backups until "
                            "the provider's backup retention period expires.",
        "user_accounts": "RETAINED: sign-in accounts belong to people, not the organization. "
                         "Members lose access to this workspace; each can ask for their account to be removed.",
        "prospect_record": "RETAINED: the original assessment and order are MeasuredField business "
                           "records kept under the retention policy.",
        "sent_email": "NOT RECALLABLE: messages already delivered by an email provider cannot be deleted.",
    }
    db.add(DeletionReceipt(org_id=org_id, receipt=receipt))
    return receipt


def revoke_org_contributions(db: TenantSession, org_id: uuid.UUID) -> int:
    """Revoke every shared skill version sourced from this organization and roll each
    affected skill back to its newest remaining earlier version (or to none)."""
    versions = db.scalars(select(SharedSkillVersion).where(
        SharedSkillVersion.source_org_id == org_id, SharedSkillVersion.status != "revoked")).all()
    for version in versions:
        version.status, version.revoked_at = "revoked", now()
        skill = db.get(SharedSkill, version.skill_id)
        if skill is not None and skill.active_version_id == version.id:
            db.flush()
            fallback = db.scalar(select(SharedSkillVersion).where(
                SharedSkillVersion.skill_id == skill.id, SharedSkillVersion.status == "superseded")
                .order_by(SharedSkillVersion.number.desc()).limit(1))
            if fallback is not None:
                fallback.status = "active"
            skill.active_version_id = fallback.id if fallback else None
    for review in db.scalars(select(LessonReview).where(LessonReview.source_org_id == org_id,
                                                         LessonReview.status.in_(("pending", "approved")))):
        review.status = "withdrawn"
    return len(versions)


def purge_expired(db: TenantSession) -> dict:
    """Retention sweep for platform tables. Safe to run repeatedly."""
    cutoff = now() - timedelta(days=30)
    return {
        "sessions": db.execute(delete(Session).where(Session.expires_at < cutoff)).rowcount,
        "auth_tokens": db.execute(delete(AuthToken).where(AuthToken.expires_at < cutoff)).rowcount,
        "rate_limits": db.execute(text("DELETE FROM rate_limits WHERE window_start < now() - interval '1 day'")).rowcount,
    }
