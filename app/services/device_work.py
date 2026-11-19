"""Encrypted, tenant-bound work-request queue for verified owner devices.

OAuth clients can queue a bounded file operation ONLY with mf:computer.write.
A DPAPI-authenticated device can lease ONLY its own work. This does not bypass
the independent owner-approved local folder grants or local session checks.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.db import TenantSession, session_scope
from app.local_connect.store import _relative_parts, MAX_FILE_BYTES
from app.mcp_connector.gateway import ConnectorAccessDenied
from app.mcp_plugin.links import linked_subject_user_id
from app.models import (ConnectedDevice, DeviceWorkRequest, Membership,
                        Organization, User, now)
from app.security import can, digest, rate_limited
from app.services.audit import audit

WORK_SCOPE = "mf:computer.write"
KEY_NAME = "MF_DEVICE_WORK_FERNET_KEY"
JOB_TTL = timedelta(minutes=10)
LEASE_TTL = timedelta(minutes=2)
REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{12,80}\Z")
PROJECT_ID = re.compile(r"[a-f0-9]{24}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class WorkQueueDenied(ValueError):
    """Do not disclose private business/device existence to unauthorized callers."""


def _box(org_id: uuid.UUID) -> MultiFernet:
    """Derive a distinct encryption key per tenant; allow a bounded rotation window.

    The master stays in the Render secret store, not in tenant tables or job
    records. A compromised master still affects all tenants; a managed KMS
    envelope key is a later hardening task.
    """
    primary = os.environ.get(KEY_NAME, "")
    previous = os.environ.get("MF_DEVICE_WORK_FERNET_PREVIOUS_KEYS", "")
    if not primary:
        raise WorkQueueDenied("Encrypted remote work is not configured")
    masters = [primary] + [key for key in previous.split(";") if key]
    if len(masters) > 4:
        raise WorkQueueDenied("Too many active encryption keys")
    try:
        boxes = []
        for master in masters:
            raw = base64.urlsafe_b64decode(master.encode("ascii"))
            if len(raw) != 32:
                raise ValueError("Invalid Fernet master key")
            derived = HKDF(
                algorithm=hashes.SHA256(), length=32,
                salt=b"MeasuredField-work-relay-v1",
                info=b"org:" + org_id.bytes,
            ).derive(raw)
            boxes.append(Fernet(base64.urlsafe_b64encode(derived)))
        return MultiFernet(boxes)
    except (ValueError, TypeError, UnicodeError):
        raise WorkQueueDenied("Encrypted remote work is not configured") from None


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise WorkQueueDenied("Business, computer, or job unavailable") from None


def _payload(action: str, project_id: str, relative_path: str,
             content: str | None, expected_sha256: str | None) -> tuple[bytes, str]:
    if action not in ("write_file", "make_directory"):
        raise WorkQueueDenied("Unsupported work action")
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise WorkQueueDenied("Select an owner-approved project")
    path_segments = _relative_parts(relative_path)
    if not path_segments or len(relative_path) > 260:
        raise WorkQueueDenied("Choose a file or folder within a project")
    if expected_sha256 is not None and (
            not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256)):
        raise WorkQueueDenied("Expected file hash must be valid SHA-256")
    if action == "make_directory":
        if content is not None or expected_sha256 is not None:
            raise WorkQueueDenied("A directory request cannot carry file contents")
    elif not isinstance(content, str):
        raise WorkQueueDenied("File content must be UTF-8 text")
    elif len(content.encode("utf-8")) > MAX_FILE_BYTES or "\x00" in content:
        raise WorkQueueDenied("File content is binary or exceeds 256 KiB")
    plain = json.dumps({
        "project_id": project_id, "relative_path": relative_path,
        "content": content if action == "write_file" else None,
        "expected_sha256": expected_sha256,
    }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return plain, hashlib.sha256((action + ":").encode() + plain).hexdigest()


def _verified_actor(bearer, *, issuer: str, audience: str) -> uuid.UUID:
    identity = getattr(bearer, "identity", None)
    if (identity is None or identity.issuer != issuer or identity.audience != audience
            or not { "mf:read", WORK_SCOPE }.issubset(identity.scopes)
            or not isinstance(bearer.issued_at, int)):
        raise ConnectorAccessDenied("Computer write authorization is unavailable")
    actor = linked_subject_user_id(identity.issuer, identity.subject, bearer.issued_at)
    if not isinstance(actor, uuid.UUID):
        raise ConnectorAccessDenied("The connected account is no longer authorized")
    return actor


def enqueue(bearer, *, issuer: str, audience: str, org_id: str, device_id: str,
            action: str, project_id: str, relative_path: str,
            content: str | None, expected_sha256: str | None, request_key: str) -> dict:
    actor = _verified_actor(bearer, issuer=issuer, audience=audience)
    org, device_id = _uuid(org_id), _uuid(device_id)
    client_id = getattr(bearer, "client_id", "")
    if (not isinstance(client_id, str) or not 1 <= len(client_id) <= 250
            or not isinstance(request_key, str) or not REQUEST_KEY.fullmatch(request_key)):
        raise WorkQueueDenied("Valid OAuth client and stable request ID required")
    data, payload_hash = _payload(
        action, project_id, relative_path, content, expected_sha256)
    cipher = _box(org).encrypt(data)

    def op() -> dict:
        with session_scope(user_id=actor) as db:
            user = db.get(User, actor)
            membership = db.scalar(select(Membership).where(
                Membership.user_id == actor, Membership.org_id == org))
            if (not user or user.disabled_at or not membership or
                    membership.role not in ("owner", "admin") or
                    not can(membership.role, "devices.manage")):
                raise ConnectorAccessDenied("This business does not permit computer work")
            db.set_context(org, actor)
            business = db.get(Organization, org)
            device = db.get(ConnectedDevice, device_id)
            if (not business or business.status != "active" or not device or
                    device.org_id != org or device.status != "active" or device.access != "work"):
                raise WorkQueueDenied("Connected Work-mode computer unavailable")
            existing = db.scalar(select(DeviceWorkRequest).where(
                DeviceWorkRequest.created_by == actor,
                DeviceWorkRequest.request_key == request_key))
            if existing:
                if (existing.device_id != device_id or existing.action != action or
                        existing.payload_hash != payload_hash or
                        existing.oauth_client_id != client_id):
                    raise WorkQueueDenied("Request ID was already used for different work")
                return _receipt(existing, repeated=True)
            pending = db.scalar(select(func.count()).select_from(DeviceWorkRequest).where(
                DeviceWorkRequest.device_id == device_id,
                DeviceWorkRequest.status.in_(("queued", "leased")))) or 0
            if pending >= 20 or rate_limited(
                db, "device-work:" + str(actor) + ":" + str(org),
                limit=80, window_seconds=3600
            ):
                raise WorkQueueDenied("Too many active work requests")
            job = DeviceWorkRequest(
                org_id=org, device_id=device_id, created_by=actor,
                oauth_client_id=client_id, request_key=request_key,
                action=action, payload_encrypted=cipher, payload_hash=payload_hash,
                expires_at=now() + JOB_TTL,
            )
            db.add(job)
            db.flush()
            audit(db, org, actor, "device.work_queued", "device_work_request", job.id,
                  {"action": action, "device_id": str(device_id)})
            return _receipt(job, repeated=False)
    try:
        return op()
    except IntegrityError:
        # Concurrent identical retries race on the per-actor request key.
        # PostgreSQL UNIQUE is the authority; re-read after rollback.
        with session_scope(user_id=actor) as db:
            member = db.scalar(select(Membership).where(
                Membership.user_id == actor, Membership.org_id == org))
            if not member or member.role not in ("owner", "admin"):
                raise ConnectorAccessDenied("Work request unavailable") from None
            db.set_context(org, actor)
            previous = db.scalar(select(DeviceWorkRequest).where(
                DeviceWorkRequest.created_by == actor,
                DeviceWorkRequest.request_key == request_key))
            if (not previous or previous.device_id != device_id or
                    previous.action != action or previous.payload_hash != payload_hash or
                    previous.oauth_client_id != client_id):
                raise WorkQueueDenied("Request cannot be replayed with changed content") from None
            return _receipt(previous, repeated=True)


def _receipt(job: DeviceWorkRequest, *, repeated: bool = False) -> dict:
    return {
        "job_id": str(job.id), "device_id": str(job.device_id),
        "state": job.status, "request_id": job.request_key,
        "expires_at": job.expires_at.isoformat(),
        "already_exists": repeated,
        "result_sha256": job.result_hash,
        "result_status": job.result_status,
    }


def status(bearer, *, issuer: str, audience: str, org_id: str, job_id: str) -> dict:
    actor = _verified_actor(bearer, issuer=issuer, audience=audience)
    org, job_key = _uuid(org_id), _uuid(job_id)
    with session_scope(user_id=actor) as db:
        member = db.scalar(select(Membership).where(
            Membership.user_id == actor, Membership.org_id == org))
        if not member or member.role not in ("owner", "admin"):
            raise ConnectorAccessDenied("Work request unavailable")
        db.set_context(org, actor)
        job = db.get(DeviceWorkRequest, job_key)
        if not job or job.created_by != actor:
            raise WorkQueueDenied("Work request unavailable")
        return _receipt(job)


def cancel_for_owner(db: TenantSession, *, org_id: uuid.UUID,
                     owner_id: uuid.UUID, job_id: str) -> dict:
    """Cancel QUEUED work before the device claims it; leased jobs need device revoke."""
    job = db.scalar(select(DeviceWorkRequest).where(
        DeviceWorkRequest.id == _uuid(job_id),
        DeviceWorkRequest.org_id == org_id,
        DeviceWorkRequest.created_by == owner_id).with_for_update())
    if not job:
        raise WorkQueueDenied("Work request unavailable")
    if job.status == "cancelled":
        return _receipt(job, repeated=True)
    if job.status != "queued":
        raise WorkQueueDenied("Already claimed or finished; disconnect the device to stop it")
    job.status, job.finished_at, job.result_status = "cancelled", now(), "owner_cancelled"
    job.lease_hash, job.lease_expires_at = None, None
    job.payload_encrypted = b""
    audit(db, org_id, owner_id, "device.work_cancelled", "device_work_request", job.id,
          {"device_id": str(job.device_id)})
    return _receipt(job)


def cancel(bearer, *, issuer: str, audience: str, org_id: str, job_id: str) -> dict:
    actor = _verified_actor(bearer, issuer=issuer, audience=audience)
    org = _uuid(org_id)
    with session_scope(user_id=actor) as db:
        user = db.get(User, actor)
        member = db.scalar(select(Membership).where(
            Membership.org_id == org, Membership.user_id == actor))
        if (not user or user.disabled_at is not None or not member or
                member.role not in ("owner", "admin") or
                not can(member.role, "devices.manage")):
            raise ConnectorAccessDenied("Work cancellation unavailable")
        db.set_context(org, actor)
        return cancel_for_owner(db, org_id=org, owner_id=actor, job_id=job_id)


def pending_for_owner(db: TenantSession, owner_id: uuid.UUID) -> list[DeviceWorkRequest]:
    """Only metadata for cancel controls; never return encrypted payload to HTML."""
    return list(db.scalars(select(DeviceWorkRequest).where(
        DeviceWorkRequest.created_by == owner_id,
        DeviceWorkRequest.status.in_(("queued", "leased")),
    ).order_by(DeviceWorkRequest.created_at.desc()).limit(30)))


def claim(db: TenantSession, device: ConnectedDevice) -> dict | None:
    if device.status != "active" or device.access != "work":
        raise WorkQueueDenied("Work mode is not authorized")
    current = now()
    expired = db.scalars(select(DeviceWorkRequest).where(
        DeviceWorkRequest.org_id == device.org_id,
        DeviceWorkRequest.device_id == device.id,
        DeviceWorkRequest.expires_at <= current,
        DeviceWorkRequest.status.in_(("queued", "leased")),
    ).with_for_update(skip_locked=True))
    for stale in expired:
        stale.status, stale.finished_at, stale.result_status = "expired", current, "expired"
        stale.lease_hash, stale.lease_expires_at = None, None
        stale.payload_encrypted = b""
    # Expired lease is retried: the local worker must compare on-disk digest
    # before execution to avoid repeating successful unacknowledged writes.
    job = db.scalar(select(DeviceWorkRequest).where(
        DeviceWorkRequest.org_id == device.org_id,
        DeviceWorkRequest.device_id == device.id,
        DeviceWorkRequest.expires_at > current,
        or_(DeviceWorkRequest.status == "queued",
            (DeviceWorkRequest.status == "leased") &
            (DeviceWorkRequest.lease_expires_at < current)),
    ).order_by(DeviceWorkRequest.created_at).with_for_update(skip_locked=True).limit(1))
    if not job:
        return None
    try:
        plain = _box(device.org_id).decrypt(job.payload_encrypted)
    except (InvalidToken, ValueError, TypeError):
        job.status, job.finished_at, job.result_status = "failed", now(), "cannot_decrypt"
        job.payload_encrypted = b""
        audit(db, job.org_id, job.created_by, "device.work_failed",
              "device_work_request", job.id, {"reason": "cannot_decrypt"})
        return None
    lease = secrets.token_urlsafe(36)
    job.status, job.lease_hash = "leased", digest(lease)
    job.lease_expires_at = min(current + LEASE_TTL, job.expires_at)
    return {"job_id": str(job.id), "action": job.action, "payload": json.loads(plain),
            "lease_token": lease, "lease_expires_at": job.lease_expires_at.isoformat()}


def finish(db: TenantSession, device: ConnectedDevice, job_id: str, lease: str,
           succeeded: bool, result_hash: str | None, error_code: str | None) -> dict:
    if device.status != "active" or device.access != "work":
        raise WorkQueueDenied("Work mode is not authorized")
    job = db.scalar(select(DeviceWorkRequest).where(
        DeviceWorkRequest.id == _uuid(job_id),
        DeviceWorkRequest.org_id == device.org_id,
        DeviceWorkRequest.device_id == device.id).with_for_update())
    if (job is None or job.status != "leased" or
            not isinstance(lease, str) or not job.lease_hash or
            not hmac.compare_digest(digest(lease), job.lease_hash) or
            not job.lease_expires_at or job.lease_expires_at < now()):
        raise WorkQueueDenied("Work lease is invalid or expired")
    if succeeded and (not isinstance(result_hash, str) or not SHA256.fullmatch(result_hash)):
        raise WorkQueueDenied("Successful work must return verified content digest")
    if error_code is not None and (not isinstance(error_code, str) or
            not re.fullmatch(r"[a-z_]{1,30}", error_code)):
        raise WorkQueueDenied("Invalid failure status")
    job.status = "succeeded" if succeeded else "failed"
    job.result_hash = result_hash if succeeded else None
    job.result_status = "verified" if succeeded else (error_code or "local_error")
    job.finished_at, job.lease_hash, job.lease_expires_at = now(), None, None
    job.payload_encrypted = b""  # no retained file contents after execution
    audit(db, job.org_id, job.created_by, "device.work_finished",
          "device_work_request", job.id,
          {"device_id": str(device.id), "status": job.status})
    return _receipt(job)
