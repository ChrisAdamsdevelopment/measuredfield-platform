"""PostgreSQL-backed job queue.

* ``enqueue`` is idempotent: the same key is stored once (ON CONFLICT DO NOTHING), and it
  joins the caller's transaction, so a job exists only if the work that needs it commits.
* Workers claim with ``FOR UPDATE SKIP LOCKED`` so several can run safely.
* Handlers are written to be re-runnable; a crash mid-job is retried with backoff and a
  job stuck in ``running`` is reclaimed after ten minutes.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, text

from ..config import get_settings
from ..db import session_scope
from ..models import EmailMessage, Job, Lead, Membership, Organization, ServiceOrder, User, now

log = logging.getLogger("mf.jobs")
MAX_ATTEMPTS = 5


def enqueue(db, kind: str, payload: dict, *, key: str, org_id: uuid.UUID | None = None,
            run_after: datetime | None = None) -> None:
    db.execute(text(
        "INSERT INTO jobs (id, kind, org_id, payload, idempotency_key, status, attempts, run_after, created_at) "
        "VALUES (:id, :kind, :org, CAST(:payload AS jsonb), :key, 'queued', 0, :run_after, now()) "
        "ON CONFLICT (idempotency_key) DO NOTHING"),
        {"id": uuid.uuid4(), "kind": kind, "org": org_id, "payload": json.dumps(payload),
         "key": key[:200], "run_after": run_after or now()})
    db.info["jobs_enqueued"] = True


# ------------------------------------------------------------------------ handlers

def _send_email(job: Job) -> None:
    from .email import deliver
    with session_scope() as db:
        message = db.get(EmailMessage, uuid.UUID(job.payload["message_id"]))
        if message is not None:
            deliver(db, message)
            if message.status == "failed":
                raise RuntimeError("email delivery failed: " + (message.error or ""))


def _provision_order(job: Job) -> None:
    from .orders import provision
    with session_scope() as db:
        order = db.scalar(select(ServiceOrder).where(ServiceOrder.id == uuid.UUID(job.payload["order_id"])).with_for_update())
        if order is not None and order.status == "paid":
            provision(db, order)


def _notify_new_lead(job: Job) -> None:
    """Tell owners and admins that an inquiry arrived. The email carries no inquiry content."""
    from .email import queue_email
    settings = get_settings()
    with session_scope(org_id=job.org_id) as db:
        lead = db.get(Lead, uuid.UUID(job.payload["lead_id"]))
        org = db.get(Organization, job.org_id)
        if lead is None or org is None:
            return
        recipients = db.execute(
            select(User.email).join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == job.org_id, Membership.role.in_(("owner", "admin")),
                   User.disabled_at.is_(None))).scalars().all()
        for email in recipients:
            message = queue_email(
                db, kind="lead_notice", to=email, org_id=job.org_id,
                subject=f"New inquiry for {org.name}",
                body=(f"A new inquiry arrived through your website.\n\nSign in to read and respond:\n"
                      f"{settings.public_base_url}/app/inquiries/{lead.id}\n"))
            enqueue(db, "send_email", {"message_id": str(message.id)}, key=f"email:{message.id}")


def _delete_org(job: Job) -> None:
    from .tenant_data import delete_org_now
    with session_scope(org_id=job.org_id) as db:
        delete_org_now(db, job.org_id)


def _auth_cleanup(job: Job) -> None:  # noqa: ARG001
    """Scrub used and expired sign-in tokens and age out sign-in message bodies."""
    from .email import cleanup_auth_artifacts
    with session_scope() as db:
        cleanup_auth_artifacts(db)


HANDLERS = {
    "auth_cleanup": _auth_cleanup,
    "send_email": _send_email,
    "provision_order": _provision_order,
    "notify_new_lead": _notify_new_lead,
    "delete_org": _delete_org,
}


def _claim() -> Job | None:
    with session_scope() as db:
        job = db.scalar(
            select(Job).where(
                ((Job.status == "queued") & (Job.run_after <= now()))
                | ((Job.status == "running") & (Job.locked_at < now() - timedelta(minutes=10))))
            .order_by(Job.run_after).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        job.status, job.locked_at, job.attempts = "running", now(), job.attempts + 1
        db.flush()
        db.expunge(job)
        return job


def run_due(limit: int = 50) -> int:
    """Run queued jobs that are due. Returns how many were attempted."""
    ran = 0
    while ran < limit:
        job = _claim()
        if job is None:
            break
        ran += 1
        error = None
        try:
            handler = HANDLERS.get(job.kind)
            if handler is None:
                raise RuntimeError("unknown job kind")
            handler(job)
        except Exception as exc:  # noqa: BLE001 - recorded and retried
            error = (type(exc).__name__ + ": " + str(exc))[:500]
            log.warning("job failed kind=%s id=%s attempt=%s error=%s", job.kind, job.id, job.attempts, type(exc).__name__)
        with session_scope() as db:
            row = db.get(Job, job.id)
            if row is None:
                continue
            if error is None:
                row.status, row.finished_at, row.last_error = "done", now(), None
            elif row.attempts >= MAX_ATTEMPTS:
                row.status, row.finished_at, row.last_error = "failed", now(), error
            else:
                row.status, row.last_error = "queued", error
                row.run_after = now() + timedelta(seconds=30 * 2 ** row.attempts)
    return ran
