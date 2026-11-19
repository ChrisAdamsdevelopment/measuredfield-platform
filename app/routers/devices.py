"""Connected computers: the owner's page (/app/computers) and the device API (/api/device/v1).

Two audiences, two authentication schemes, never mixed:

* /app/computers is for people. It uses the workspace session, roles and CSRF token.
* /api/device/v1 is for a paired computer. It accepts ONLY ``Authorization: Bearer`` with a
  device credential. Browser cookies are ignored on these routes, so a signed-in person's
  session can never act as a device and a device credential can never open the workspace.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..db import TenantSession, get_db
from ..deps import Principal, back, client_key, member, member_post, parse_uuid
from ..models import ConnectedDevice, Organization, User
from ..security import rate_limited
from ..services import devices, device_work
from .workspace import _page

router = APIRouter()
UNAUTHORIZED = {"error": "This computer is not connected. Connect it again from your MeasuredField workspace."}


# ------------------------------------------------------------------------- owner pages

def _computers_page(request: Request, db: TenantSession, p: Principal, *, issued=None, status_code: int = 200):
    rows = devices.list_devices(db)
    people = {u.id: (u.name or u.email) for u in db.scalars(
        select(User).where(User.id.in_([d.user_id for d in rows if d.user_id] or [None])))}
    return _page(request, db, p, "app/computers.html", {
        "devices": rows, "people": people, "codes": devices.open_codes(db, p.org.id), "issued": issued,
        "access_levels": devices.ACCESS_LEVELS, "activity": devices.recent_activity(db, 30),
        "can_manage": p.can("devices.manage"),
        "pending_work": device_work.pending_for_owner(db, p.user.id)
        if p.role in ("owner", "admin") and p.can("devices.manage") else [],
    }, status_code=status_code)


@router.get("/app/computers")
def computers(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    return _computers_page(request, db, p)


@router.post("/app/computers/codes")
def computer_code(request: Request, device_name: str = Form(""), access: str = Form("read"),
                  db: TenantSession = Depends(get_db), p: Principal = Depends(member_post("devices.manage"))):
    try:
        issued = devices.create_pairing_code(db, p.org.id, p.user.id, device_name=device_name, access=access)
    except devices.DeviceError as exc:
        return back("/app/computers", err=str(exc))
    # Rendered directly (no redirect) so the code is shown once and never appears in a URL.
    response = _computers_page(request, db, p, issued=issued)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/app/computers/codes/{code_id}/cancel")
def computer_code_cancel(code_id: str, db: TenantSession = Depends(get_db),
                         p: Principal = Depends(member_post("devices.manage"))):
    if not devices.cancel_pairing_code(db, p.org.id, p.user.id, parse_uuid(code_id)):
        raise HTTPException(404, "Not found")
    return back("/app/computers", ok="Code cancelled")


def _device(db: TenantSession, device_id: str) -> ConnectedDevice:
    device = db.get(ConnectedDevice, parse_uuid(device_id))    # row-level security: other tenants' rows do not exist
    if device is None:
        raise HTTPException(404, "Not found")
    return device


@router.post("/app/computers/{device_id}/confirm")
def computer_confirm(device_id: str, check_number: str = Form(""), db: TenantSession = Depends(get_db),
                     p: Principal = Depends(member_post("devices.manage"))):
    device = _device(db, device_id)
    try:
        devices.confirm_device(db, device, p.user.id, check_number)
    except devices.DeviceError as exc:
        db.commit()          # keep the failed-attempt counter even though the answer is an error
        db.set_context(p.org.id, p.user.id)
        return back("/app/computers", err=str(exc))
    return back("/app/computers", ok="This computer is now connected")


@router.post("/app/computers/{device_id}/disconnect")
def computer_disconnect(device_id: str, db: TenantSession = Depends(get_db),
                        p: Principal = Depends(member_post("devices.manage"))):
    device = _device(db, device_id)
    devices.disconnect_device(db, device, p.user.id, reason="owner_disconnected")
    return back("/app/computers", ok="Disconnected. That computer can no longer reach your business.")


@router.post("/app/computers/work/{job_id}/cancel")
def computer_work_cancel(job_id: str, db: TenantSession = Depends(get_db),
                         p: Principal = Depends(member_post("devices.manage"))):
    """Owner action: stop a queued work request before the desktop claims it."""
    if p.role not in ("owner", "admin"):
        raise HTTPException(403, "Owner or administrator access required")
    try:
        device_work.cancel_for_owner(
            db, org_id=p.org.id, owner_id=p.user.id, job_id=job_id)
    except device_work.WorkQueueDenied:
        return back("/app/computers", err="Cannot cancel this work request. It may already be running.")
    return back("/app/computers", ok="Pending computer work cancelled")


# -------------------------------------------------------------------------- device API

class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=24)
    device_name: str = Field(default="", max_length=120)
    platform: Literal["windows", "macos", "linux"]


class ActivityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["connection_started", "connection_ended", "folder_shared", "folder_unshared",
                    "file_read", "change_proposed", "change_approved", "change_rejected",
                    "file_written", "directory_created"]
    result: Literal["ok", "denied", "error"]
    project_label: str = Field(default="", max_length=80)
    count: int = Field(default=1, ge=0, le=10_000)
    occurred_at: datetime


class ActivityBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[ActivityEvent] = Field(min_length=1, max_length=devices.MAX_EVENTS_PER_BATCH)


def _denied() -> JSONResponse:
    return JSONResponse(UNAUTHORIZED, status_code=401, headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})


def device_auth(request: Request, db: TenantSession = Depends(get_db),
                authorization: str | None = Header(default=None)) -> ConnectedDevice:
    """Bearer device credential only. Cookies are never consulted on device routes."""
    scheme, _, credential = (authorization or "").partition(" ")
    try:
        if scheme.lower() != "bearer":
            raise devices.DeviceAuthError()
        device = devices.authenticate(db, credential.strip())
    except devices.DeviceAuthError:
        limited = rate_limited(db, "device-auth:" + client_key(request), limit=60, window_seconds=600)
        db.commit()
        raise HTTPException(429 if limited else 401, UNAUTHORIZED["error"]) from None
    return device


def _status_body(db: TenantSession, device: ConnectedDevice) -> dict:
    org = db.get(Organization, device.org_id)
    return {"device_id": str(device.id), "status": device.status, "access": device.access,
            "access_label": devices.ACCESS_LEVELS[device.access], "business": org.name if org else "",
            "name": device.name}


@router.post("/api/device/v1/pair")
def api_pair(body: PairRequest, request: Request, db: TenantSession = Depends(get_db)):
    """Redeem a pairing code. Unauthenticated by nature, so it is throttled per client and
    globally, answers every failure identically, and the result is useless until confirmed."""
    limited = rate_limited(db, "device-pair:" + client_key(request), limit=10, window_seconds=600)
    limited = rate_limited(db, "device-pair-all", limit=600, window_seconds=600) or limited
    db.commit()
    if limited:
        return JSONResponse({"error": "Too many attempts. Wait a few minutes and try again."}, status_code=429)
    try:
        result = devices.redeem_pairing_code(db, body.code, reported_name=body.device_name, platform=body.platform)
    except devices.DeviceAuthError:
        return JSONResponse({"error": "That code is not valid. Codes expire after ten minutes and work once."},
                            status_code=400, headers={"Cache-Control": "no-store"})
    except devices.DeviceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse({
        "device_id": str(result.device_id), "credential": result.credential, "status": "pending",
        "check_number": result.check_number, "confirm_by": result.confirm_by.isoformat(),
        "next": "Show the check number to the owner. They confirm it in their workspace under Computers."},
        status_code=201, headers={"Cache-Control": "no-store"})


@router.get("/api/device/v1/status")
def api_status(db: TenantSession = Depends(get_db), device: ConnectedDevice = Depends(device_auth)):
    if device.status == "active":
        devices.touch(device)
    return _status_body(db, device)


def _active(device: ConnectedDevice) -> ConnectedDevice:
    try:
        return devices.require_active(device)
    except devices.DeviceAuthError:
        raise HTTPException(403, "This computer is waiting for the owner to confirm it.") from None


@router.post("/api/device/v1/heartbeat")
def api_heartbeat(db: TenantSession = Depends(get_db), device: ConnectedDevice = Depends(device_auth)):
    devices.touch(_active(device))
    return _status_body(db, device)


@router.post("/api/device/v1/rotate")
def api_rotate(db: TenantSession = Depends(get_db), device: ConnectedDevice = Depends(device_auth)):
    credential = devices.rotate_credential(db, _active(device))
    return JSONResponse({"credential": credential, "note": "Store this now. The previous credential no longer works."},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/device/v1/disconnect")
def api_disconnect(db: TenantSession = Depends(get_db), device: ConnectedDevice = Depends(device_auth)):
    devices.disconnect_device(db, device, None, reason="disconnected_on_computer")
    return {"status": "revoked"}


@router.post("/api/device/v1/activity")
def api_activity(body: ActivityBatch, db: TenantSession = Depends(get_db),
                 device: ConnectedDevice = Depends(device_auth)):
    _active(device)
    items = [devices.ActivityItem(action=e.action, result=e.result, project_label=e.project_label,
                                  count=e.count, occurred_at=e.occurred_at) for e in body.events]
    try:
        stored = devices.record_activity(db, device, items)
    except devices.DeviceError as exc:
        raise HTTPException(422, str(exc)) from None
    devices.touch(device)
    return {"stored": stored}

# ----------------------------------------------------------------- queued owner Work mode

class WorkFinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: str = Field(min_length=40, max_length=128)
    succeeded: bool
    result_sha256: str | None = Field(default=None, max_length=64)
    error_code: str | None = Field(default=None, max_length=30)


@router.post("/api/device/v1/work/claim")
def api_claim_work(db: TenantSession = Depends(get_db),
                   device: ConnectedDevice = Depends(device_auth)):
    """Device-only outbound polling: no browser cookies or unsanctioned tunnel."""
    _active(device)
    try:
        job = device_work.claim(db, device)
    except device_work.WorkQueueDenied:
        raise HTTPException(403, "Computer work is not currently available") from None
    devices.touch(device)
    return JSONResponse({"job": job}, headers={"Cache-Control": "no-store"})


@router.post("/api/device/v1/work/{job_id}/finish")
def api_finish_work(job_id: str, body: WorkFinishRequest,
                    db: TenantSession = Depends(get_db),
                    device: ConnectedDevice = Depends(device_auth)):
    """Receipt accepted only with an active, device-bound short-lived lease."""
    _active(device)
    try:
        result = device_work.finish(
            db, device, job_id, body.lease_token, body.succeeded,
            body.result_sha256, body.error_code)
    except device_work.WorkQueueDenied:
        raise HTTPException(403, "Work lease is not valid") from None
    devices.touch(device)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
