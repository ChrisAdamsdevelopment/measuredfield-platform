"""Account-linked trusted computers (MF-032).

This is the server-side half of "Connect this computer". It answers one question the local
companion (app/local_connect, app/paired_connect) cannot answer by itself: WHICH MeasuredField
business and person does a given computer belong to, and is it still allowed to connect?

Flow
  1. A signed-in owner or admin asks for a pairing code in the workspace. The code is short,
     single-use, valid for ten minutes and stored only as a hash.
  2. The computer redeems the code. It receives a device credential ONCE and a six-digit
     check number, and stays ``pending``: a pending computer can do nothing but ask whether
     it has been confirmed.
  3. The owner types the check number shown on the computer into the workspace. Only then
     does the computer become ``active``. A guessed or stolen code is therefore not enough:
     the attacker's computer would show a number the owner never sees on their own machine.
  4. "Disconnect now" revokes the computer. Every device request re-reads the row, so the
     very next request is refused.

What this module deliberately does NOT do: it grants no file access, runs nothing, and holds
no file content. Folder permissions stay on the owner's computer. The only thing a computer
may send here is its status and a strictly limited activity summary.

Tenant isolation: connected_devices and device_events are tenant tables under forced
row-level security. A credential names its organization (``mfd1.<org>.<secret>``); that part
only selects which tenant to look in, and the row is found by the hash of the whole
credential, so a forged organization part finds nothing.
"""
from __future__ import annotations

import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select

from ..db import TenantSession
from ..models import ConnectedDevice, DeviceEvent, DevicePairingCode, DeviceWorkRequest, now
from ..security import digest
from .audit import audit

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"      # no 0/O, 1/I/L, U
CODE_LENGTH = 8
CODE_TTL = timedelta(minutes=10)
CONFIRM_WINDOW = timedelta(minutes=10)
MAX_ACTIVE_DEVICES = 10
MAX_OPEN_CODES = 5
MAX_CONFIRM_ATTEMPTS = 5
CREDENTIAL_PREFIX = "mfd1"
ACCESS_LEVELS = {"read": "Read only", "suggest": "Suggest changes", "work": "Work in approved projects"}
PLATFORMS = ("windows", "macos", "linux")
ACTIVITY_ACTIONS = ("connection_started", "connection_ended", "folder_shared", "folder_unshared",
                    "file_read", "change_proposed", "change_approved", "change_rejected",
                    "file_written", "directory_created")
ACTIVITY_RESULTS = ("ok", "denied", "error")
MAX_EVENTS_PER_BATCH = 50
_LABEL_FORBIDDEN = re.compile(r"[\\/:\x00-\x1f\x7f]")


class DeviceError(ValueError):
    """A request that cannot be honoured; the message is safe to show to the owner."""


class DeviceAuthError(Exception):
    """Credential missing, malformed, unknown or revoked. Deliberately carries no detail."""


@dataclass(frozen=True)
class IssuedCode:
    code: str                  # shown to the owner once; never stored
    expires_at: datetime
    record_id: uuid.UUID


@dataclass(frozen=True)
class PairingResult:
    device_id: uuid.UUID
    credential: str            # returned to the computer once; only its hash is stored
    check_number: str
    confirm_by: datetime


def clean_name(value: str, *, limit: int = 80) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value or "")
    return " ".join(value.split())[:limit]


def normalize_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def format_code(code: str) -> str:
    return code[:4] + "-" + code[4:]


# ----------------------------------------------------------------- owner side (workspace)

def create_pairing_code(db: TenantSession, org_id: uuid.UUID, user_id: uuid.UUID, *,
                        device_name: str, access: str) -> IssuedCode:
    """Requires a tenant context for ``org_id``; the caller has already checked permission."""
    if access not in ACCESS_LEVELS:
        raise DeviceError("Choose Read only, Suggest changes, or Work in approved projects.")
    device_name = clean_name(device_name)
    if len(device_name) < 2:
        raise DeviceError("Give this computer a name you will recognise, like \"Front office PC\".")
    current = now()
    for stale in db.scalars(select(DevicePairingCode).where(
            DevicePairingCode.org_id == org_id, DevicePairingCode.expires_at <= current)):
        db.delete(stale)
    db.flush()
    open_codes = db.scalar(select(func.count()).select_from(DevicePairingCode).where(
        DevicePairingCode.org_id == org_id, DevicePairingCode.used_at.is_(None))) or 0
    if open_codes >= MAX_OPEN_CODES:
        raise DeviceError("There are already several unused codes. Cancel one or wait ten minutes.")
    active = db.scalar(select(func.count()).select_from(ConnectedDevice).where(
        ConnectedDevice.status.in_(("active", "pending")))) or 0
    if active >= MAX_ACTIVE_DEVICES:
        raise DeviceError("This business has reached its limit of connected computers. Disconnect one first.")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    record = DevicePairingCode(code_hash=digest("pair:" + code), org_id=org_id, user_id=user_id,
                               device_name=device_name, access=access, expires_at=current + CODE_TTL)
    db.add(record)
    db.flush()
    audit(db, org_id, user_id, "device.code_created", "device_pairing_code", record.id, {"access": access})
    return IssuedCode(code=format_code(code), expires_at=record.expires_at, record_id=record.id)


def cancel_pairing_code(db: TenantSession, org_id: uuid.UUID, user_id: uuid.UUID, record_id: uuid.UUID) -> bool:
    record = db.scalar(select(DevicePairingCode).where(
        DevicePairingCode.id == record_id, DevicePairingCode.org_id == org_id, DevicePairingCode.used_at.is_(None)))
    if record is None:
        return False
    db.delete(record)
    audit(db, org_id, user_id, "device.code_cancelled", "device_pairing_code", record_id)
    return True


def open_codes(db: TenantSession, org_id: uuid.UUID) -> list[DevicePairingCode]:
    return list(db.scalars(select(DevicePairingCode).where(
        DevicePairingCode.org_id == org_id, DevicePairingCode.used_at.is_(None),
        DevicePairingCode.expires_at > now()).order_by(DevicePairingCode.created_at)))


def _expire_if_stale(device: ConnectedDevice) -> None:
    if device.status == "pending" and device.created_at + CONFIRM_WINDOW <= now():
        device.status, device.check_number = "expired", ""


def list_devices(db: TenantSession) -> list[ConnectedDevice]:
    devices = list(db.scalars(select(ConnectedDevice).order_by(ConnectedDevice.created_at.desc()).limit(100)))
    for device in devices:
        _expire_if_stale(device)
    return devices


def confirm_device(db: TenantSession, device: ConnectedDevice, user_id: uuid.UUID, check_number: str) -> None:
    """The owner proves they are looking at the same computer by typing its check number."""
    _expire_if_stale(device)
    if device.status != "pending":
        raise DeviceError("This computer is no longer waiting for confirmation. Start again with a new code.")
    typed = re.sub(r"\D", "", check_number or "")
    if not hmac.compare_digest(typed.encode(), device.check_number.encode()):
        device.confirm_attempts += 1
        if device.confirm_attempts >= MAX_CONFIRM_ATTEMPTS:
            device.status, device.check_number = "expired", ""
        audit(db, device.org_id, user_id, "device.confirm_failed", "connected_device", device.id,
              {"attempts": device.confirm_attempts})
        raise DeviceError("That number does not match the one on the computer. Nothing was connected.")
    device.status, device.confirmed_at, device.confirmed_by, device.check_number = "active", now(), user_id, ""
    audit(db, device.org_id, user_id, "device.confirmed", "connected_device", device.id, {"access": device.access})


def disconnect_device(db: TenantSession, device: ConnectedDevice, user_id: uuid.UUID | None, *, reason: str) -> bool:
    """Revoke. The stored hash is replaced as well, so even a copy of the database taken
    afterwards holds nothing that matches the old credential."""
    if device.status == "revoked":
        return False
    device.status, device.revoked_at, device.revoked_by, device.revoked_reason = "revoked", now(), user_id, reason[:40]
    device.credential_hash, device.check_number = digest(secrets.token_urlsafe(32)), ""
    # A revoked computer must not retain leased customer work or encrypted
    # file contents indefinitely. Scrub all outstanding payloads immediately.
    pending = db.scalars(select(DeviceWorkRequest).where(
        DeviceWorkRequest.org_id == device.org_id,
        DeviceWorkRequest.device_id == device.id,
        DeviceWorkRequest.status.in_(("queued", "leased")),
    ).with_for_update())
    for job in pending:
        job.status, job.finished_at, job.result_status = "cancelled", now(), "device_revoked"
        job.lease_hash, job.lease_expires_at = None, None
        job.payload_encrypted = b""
    audit(db, device.org_id, user_id, "device.disconnected", "connected_device", device.id, {"reason": reason[:40]})
    return True


def recent_activity(db: TenantSession, limit: int = 50) -> list[tuple[DeviceEvent, str]]:
    return list(db.execute(select(DeviceEvent, ConnectedDevice.name)
                           .join(ConnectedDevice, ConnectedDevice.id == DeviceEvent.device_id)
                           .order_by(DeviceEvent.occurred_at.desc()).limit(limit)).all())


# ------------------------------------------------------------------ computer side (API)

def _new_credential(org_id: uuid.UUID) -> str:
    return f"{CREDENTIAL_PREFIX}.{org_id.hex}.{secrets.token_urlsafe(32)}"


def redeem_pairing_code(db: TenantSession, code: str, *, reported_name: str, platform: str) -> PairingResult:
    """Called with NO tenant context. The pairing-code table is a platform table holding only
    a hash and the ids needed to enter the right tenant."""
    if platform not in PLATFORMS:
        raise DeviceError("Unsupported computer type.")
    normalized = normalize_code(code)
    if len(normalized) != CODE_LENGTH:
        raise DeviceAuthError()
    record = db.scalar(select(DevicePairingCode).where(
        DevicePairingCode.code_hash == digest("pair:" + normalized)).with_for_update())
    if record is None or record.used_at is not None or record.expires_at <= now():
        raise DeviceAuthError()
    record.used_at = now()
    db.set_context(record.org_id, None)
    credential = _new_credential(record.org_id)
    check_number = f"{secrets.randbelow(1_000_000):06d}"
    device = ConnectedDevice(
        org_id=record.org_id, user_id=record.user_id, name=record.device_name,
        reported_name=clean_name(reported_name), platform=platform, access=record.access,
        status="pending", credential_hash=digest(credential), check_number=check_number)
    db.add(device)
    db.flush()
    audit(db, record.org_id, None, "device.pairing_requested", "connected_device", device.id, {"platform": platform})
    return PairingResult(device_id=device.id, credential=credential, check_number=check_number,
                         confirm_by=device.created_at + CONFIRM_WINDOW)


def authenticate(db: TenantSession, credential: str | None) -> ConnectedDevice:
    """Resolve a device credential to its row inside its own tenant, or raise DeviceAuthError.

    Returns pending devices too (they may only ask for their status); callers that need an
    active computer must use ``require_active``.
    """
    if not credential or len(credential) > 200:
        raise DeviceAuthError()
    parts = credential.split(".")
    if len(parts) != 3 or parts[0] != CREDENTIAL_PREFIX or len(parts[2]) < 32:
        raise DeviceAuthError()
    try:
        org_id = uuid.UUID(hex=parts[1])
    except ValueError:
        raise DeviceAuthError() from None
    db.set_context(org_id, None)
    device = db.scalar(select(ConnectedDevice).where(ConnectedDevice.credential_hash == digest(credential)))
    if device is None:
        db.set_context(None, None)
        raise DeviceAuthError()
    _expire_if_stale(device)
    if device.status not in ("pending", "active"):
        raise DeviceAuthError()
    return device


def require_active(device: ConnectedDevice) -> ConnectedDevice:
    if device.status != "active":
        raise DeviceAuthError()
    return device


def touch(device: ConnectedDevice) -> None:
    if device.last_seen_at is None or now() - device.last_seen_at > timedelta(seconds=30):
        device.last_seen_at = now()


def rotate_credential(db: TenantSession, device: ConnectedDevice) -> str:
    """Issue a new credential and invalidate the old one in the same transaction."""
    require_active(device)
    credential = _new_credential(device.org_id)
    device.credential_hash, device.credential_rotated_at = digest(credential), now()
    audit(db, device.org_id, None, "device.credential_rotated", "connected_device", device.id)
    return credential


@dataclass(frozen=True)
class ActivityItem:
    action: Literal["connection_started", "connection_ended", "folder_shared", "folder_unshared",
                    "file_read", "change_proposed", "change_approved", "change_rejected",
                    "file_written", "directory_created"]
    result: Literal["ok", "denied", "error"]
    project_label: str
    count: int
    occurred_at: datetime


def record_activity(db: TenantSession, device: ConnectedDevice, items: list[ActivityItem]) -> int:
    """Store a summary of what happened on the computer. Metadata only.

    A project label is a name the owner chose ("My Website"). Anything that looks like a path
    is refused, so file locations never reach the server. A Read-only computer may not report
    change events as successful; that would mean the local permission was bypassed.
    """
    require_active(device)
    if len(items) > MAX_EVENTS_PER_BATCH:
        raise DeviceError("Too many events in one request.")
    current = now()
    stored = 0
    for item in items:
        if item.action not in ACTIVITY_ACTIONS or item.result not in ACTIVITY_RESULTS:
            raise DeviceError("Unknown activity type.")
        label = clean_name(item.project_label, limit=80)
        if _LABEL_FORBIDDEN.search(item.project_label or "") or not 0 <= item.count <= 10_000:
            raise DeviceError("Activity must not contain file paths.")
        if (device.access == "read" and item.action in
                ("change_proposed", "change_approved", "file_written", "directory_created")
                and item.result == "ok"):
            raise DeviceError("This computer is connected as Read only.")
        if (device.access != "work" and item.action in ("file_written", "directory_created")
                and item.result == "ok"):
            raise DeviceError("Direct file changes require owner-approved Work mode.")
        occurred = item.occurred_at
        if occurred.tzinfo is None or occurred > current + timedelta(minutes=5) or occurred < current - timedelta(days=30):
            occurred = current
        db.add(DeviceEvent(org_id=device.org_id, device_id=device.id, action=item.action, result=item.result,
                           project_label=label, count=item.count, occurred_at=occurred))
        stored += 1
    return stored
