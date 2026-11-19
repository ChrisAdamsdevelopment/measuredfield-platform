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

from sqlalchemy import func, select, text, update

from ..config import get_settings
from ..db import session_scope
from ..models import EmailMessage, Job, Lead, LeadReply, Membership, Organization, ServiceOrder, User, now

log = logging.getLogger("mf.jobs")
MAX_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 900
STALE_AFTER = timedelta(minutes=10)


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
                raise RuntimeError("email delivery failed")


def _send_lead_reply(job: Job) -> None:
    """Deliver only owner-approved tenant replies, idempotently within provider limits.

    Provider acceptance is recorded; final mailbox delivery is never inferred.
    Past the provider's 24-hour key retention window, stop and require reconciliation
    rather than risk duplicating a message after an unknown network result.
    """
    from .email import get_provider
    from .audit import audit

    failed = False
    try:
        reply_id = uuid.UUID(job.payload["reply_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("invalid lead reply job") from exc
    with session_scope(org_id=job.org_id) as db:
        reply = db.scalar(select(LeadReply).where(
            LeadReply.id == reply_id, LeadReply.org_id == job.org_id).with_for_update())
        if reply is None or reply.status not in ("queued", "failed"):
            return
        if reply.approved_at is None or reply.approved_by is None:
            raise RuntimeError("unapproved reply cannot be sent")
        if now() - reply.approved_at >= timedelta(hours=20):
            reply.status = "failed"
            reply.last_error = "manual_reconciliation_required"
            audit(db, reply.org_id, None, "lead.reply_manual_reconciliation",
                  "lead_reply", reply.id, {"lead_id": str(reply.lead_id)})
            return
        provider = get_provider()
        if provider is None or provider.name != "resend":
            reply.status = "not_configured"
            reply.last_error = "email_provider_unavailable"
            return
        try:
            result = provider.send(
                to=reply.to_email, subject=reply.subject, body=reply.body,
                idempotency_key=f"lead-reply/{reply.id}",
            )
            accepted = result.ok and bool(result.provider_message_id)
        except Exception:  # noqa: BLE001 - provider exceptions can contain PII
            accepted = False
            result = None
        if accepted:
            reply.status = "accepted"
            reply.provider_message_id = result.provider_message_id
            reply.accepted_at = now()
            reply.last_error = None
            lead = db.scalar(select(Lead).where(
                Lead.id == reply.lead_id, Lead.org_id == reply.org_id).with_for_update())
            if lead is not None:
                if lead.first_response_at is None:
                    lead.first_response_at = reply.accepted_at
                if lead.status == "new":
                    lead.status = "contacted"
        else:
            reply.status = "failed"
            reply.last_error = "provider_not_accepted"
            failed = True
    if failed:
        raise RuntimeError("lead_reply_provider_failure")


def _provision_order(job: Job) -> None:
    from .orders import provision
    with session_scope() as db:
        order = db.scalar(select(ServiceOrder).where(ServiceOrder.id == uuid.UUID(job.payload["order_id"])).with_for_update())
        if order is not None and order.status == "paid":
            provision(db, order)


def _notify_new_lead(job: Job) -> None:
    """Queue at most one notice per lead+owner email, even after a job replay.

    Both lookup and insertion happen under a lock on the same tenant Lead row;
    the *message* and deterministic send job commit in one transaction.
    Existing legacy notice messages are reused when their stable inquiry link
    is present, so deployed pre-upgrade jobs also avoid duplicate notifications.
    """
    from .email import queue_email

    settings = get_settings()
    with session_scope(org_id=job.org_id) as db:
        lead_id = uuid.UUID(job.payload["lead_id"])
        lead = db.scalar(select(Lead).where(
            Lead.id == lead_id, Lead.org_id == job.org_id).with_for_update())
        org = db.get(Organization, job.org_id)
        if lead is None or org is None:
            return
        recipients = db.execute(
            select(User.email).join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == job.org_id, Membership.role.in_(("owner", "admin")),
                   User.disabled_at.is_(None))).scalars().all()
        stable_lead_link = f"/app/inquiries/{lead.id}\n"
        body = ("A new inquiry arrived through your website.\n\nSign in to read and respond:\n"
                f"{settings.public_base_url}{stable_lead_link}")
        for email in sorted(set(recipients)):
            # The previous implementation used random message IDs for notices.
            # Reuse a prior note for this lead/recipient to remain compatible.
            message = db.scalar(
                select(EmailMessage).where(
                    EmailMessage.org_id == job.org_id,
                    EmailMessage.kind == "lead_notice",
                    EmailMessage.to_email == email,
                    EmailMessage.body.contains(stable_lead_link),
                ).order_by(EmailMessage.created_at, EmailMessage.id).limit(1)
            )
            if message is None:
                message = queue_email(
                    db, kind="lead_notice", to=email, org_id=job.org_id,
                    subject=f"New inquiry for {org.name}", body=body)
            enqueue(db, "send_email", {"message_id": str(message.id)},
                    key=f"email:{message.id}")


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
    "send_lead_reply": _send_lead_reply,
    "provision_order": _provision_order,
    "notify_new_lead": _notify_new_lead,
    "delete_org": _delete_org,
}


def _claim() -> Job | None:
    current = now()
    stale = ((Job.status == "running") &
             ((Job.locked_at.is_(None)) | (Job.locked_at < current - STALE_AFTER)))
    with session_scope() as db:
        # A process that dies *after* its final allowed attempt must not be
        # reclaimed forever. Mark it terminal once its lease has expired.
        exhausted = (Job.attempts >= MAX_ATTEMPTS) & (
            (Job.status == "queued") | stale)
        db.execute(update(Job).where(exhausted).values(
            status="failed", finished_at=current,
            last_error="attempt limit reached after worker interruption"))
        job = db.scalar(
            select(Job).where(
                Job.attempts < MAX_ATTEMPTS,
                ((Job.status == "queued") & (Job.run_after <= current)) | stale)
            .order_by(Job.run_after, Job.created_at)
            .limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        job.status, job.locked_at, job.attempts = "running", current, job.attempts + 1
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
        handler = HANDLERS.get(job.kind)
        try:
            if handler is None:
                raise RuntimeError("unknown job kind")
            handler(job)
        except Exception as exc:  # noqa: BLE001 - recorded and retried
            # Never persist an exception body: providers may include recipient,
            # raw email text, private inquiry or secret tokens in exceptions.
            error = "unknown job kind" if handler is None else type(exc).__name__[:80]
            log.warning("job failed kind=%s id=%s attempt=%s error=%s",
                        job.kind, job.id, job.attempts, type(exc).__name__)
        with session_scope() as db:
            row = db.scalar(select(Job).where(Job.id == job.id).with_for_update())
            if row is None:
                continue
            # A stale worker cannot overwrite a newer attempt or a terminal
            # outcome after another worker reclaimed this job's expired lease.
            if row.status != "running" or row.attempts != job.attempts:
                log.warning("stale job completion ignored kind=%s id=%s expected_attempt=%s",
                            job.kind, job.id, job.attempts)
                continue
            if error is None:
                row.status, row.finished_at, row.last_error = "done", now(), None
            elif row.attempts >= MAX_ATTEMPTS:
                row.status, row.finished_at, row.last_error = "failed", now(), error
            else:
                row.status, row.last_error = "queued", error
                row.run_after = now() + timedelta(seconds=min(MAX_BACKOFF_SECONDS, 30 * 2 ** row.attempts))
    return ran



def job_status_snapshot(*, failures_limit: int = 20) -> dict:
    """Operator-shell-only queue status, with no payload or private error text.

    Deliberately not mounted as an HTTP route. Credentials and database access
    are supplied by the authorized operator runtime; individual tenant
    identities and customer message contents are never returned.
    """
    if type(failures_limit) is not int or not 1 <= failures_limit <= 100:
        raise ValueError("failures_limit must be an integer from 1 to 100")
    current = now()
    statuses = ("queued", "running", "done", "failed")
    with session_scope() as db:
        counts = {
            status: int(db.scalar(
                select(func.count()).select_from(Job).where(Job.status == status)
            ) or 0) for status in statuses
        }
        due = int(db.scalar(select(func.count()).select_from(Job).where(
            Job.status == "queued", Job.run_after <= current)) or 0)
        stale = int(db.scalar(select(func.count()).select_from(Job).where(
            Job.status == "running",
            ((Job.locked_at.is_(None)) |
             (Job.locked_at < current - STALE_AFTER)))) or 0)
        records = db.execute(
            select(Job.id, Job.kind, Job.attempts, Job.finished_at)
            .where(Job.status == "failed")
            .order_by(Job.finished_at.desc().nullslast(), Job.created_at.desc())
            .limit(failures_limit)).all()
        failures = [
            {
                "job_id": str(record.id),
                "kind": record.kind if record.kind in HANDLERS else "unknown",
                "attempts": record.attempts,
                "finished_at_utc": record.finished_at.isoformat()
                if record.finished_at else None,
            } for record in records
        ]
    return {
        "observed_at_utc": current.isoformat(),
        "counts": counts,
        "due_queued": due,
        "stale_running": stale,
        "failed_jobs": failures,
        "failed_limit": failures_limit,
    }
