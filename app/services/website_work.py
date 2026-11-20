"""Real user-to-ChatGPT website work requests and optimistic draft patch execution.

No synthetic 'done', no server-side model invocation, and NEVER publish here.
Both website browser and OAuth MCP entrypoints use these same auditable actions.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from sqlalchemy import select

from app.db import session_scope
from app.mcp_connector.gateway import ConnectorAccessDenied
from app.mcp_plugin.links import linked_subject_user_id
from app.models import Membership, Organization, Site, User, WebsiteWorkRequest, now
from app.security import can, rate_limited
from app.services import sitegen, sites
from app.services.audit import audit

WRITE_SCOPE = "mf:website.write"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_BAD_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_ALLOWED_PATCH_KEYS = frozenset({"headline", "intro", "about", "services", "color"})


class WebsiteWorkDenied(ValueError):
    """Web site work has no tenant, permission, or valid baseline."""


def _id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError):
        raise WebsiteWorkDenied("Website work is unavailable") from None


def _hash(content: object) -> str:
    return hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()


def _instruction(text: str) -> str:
    if not isinstance(text, str):
        raise WebsiteWorkDenied("Describe the website change you need")
    value = text.strip()
    if not 12 <= len(value) <= 2400 or _BAD_CONTROL.search(value):
        raise WebsiteWorkDenied("Enter 12 to 2,400 readable characters")
    return value


def _patch(raw: dict) -> tuple[dict, str]:
    if not isinstance(raw, dict) or not raw or not set(raw).issubset(_ALLOWED_PATCH_KEYS):
        raise WebsiteWorkDenied("Only website content and brand color may be changed")
    for key, value in raw.items():
        if key == "services":
            if not isinstance(value, (str, list)):
                raise WebsiteWorkDenied("Services must be structured website content")
            if isinstance(value, list) and (len(value) > 8 or any(not isinstance(x, dict) for x in value)):
                raise WebsiteWorkDenied("Services must be a short list of named services")
        elif not isinstance(value, str) or _BAD_CONTROL.search(value):
            raise WebsiteWorkDenied("Website text must be readable text")
    size = len(json.dumps(raw, ensure_ascii=False))
    if size > 7500:
        raise WebsiteWorkDenied("Website content update exceeds the allowed size")
    return raw, _hash(raw)


def enqueue(db, org_id: uuid.UUID, actor: uuid.UUID, site: Site,
            instruction: str) -> WebsiteWorkRequest:
    if site.org_id != org_id:
        raise WebsiteWorkDenied("Website unavailable")
    msg = _instruction(instruction)
    pending = db.scalars(select(WebsiteWorkRequest.id).where(
        WebsiteWorkRequest.org_id == org_id,
        WebsiteWorkRequest.created_by == actor,
        WebsiteWorkRequest.status == "queued").limit(31)).all()
    if len(pending) >= 30 or rate_limited(
        db, "website-work-queue:" + str(actor) + ":" + str(org_id), limit=50,
        window_seconds=3600,
    ):
        raise WebsiteWorkDenied("Too many outstanding website requests")
    work = WebsiteWorkRequest(
        org_id=org_id, site_id=site.id, created_by=actor,
        instruction=msg, status="queued",
        expected_draft_sha256=_hash(site.draft),
    )
    db.add(work)
    db.flush()
    audit(db, org_id, actor, "website.work_requested",
          "website_work_request", work.id, {"site_id": str(site.id)})
    return work


def cancel_for_owner(db, *, org_id: uuid.UUID, actor: uuid.UUID,
                     request_id: str) -> WebsiteWorkRequest:
    item = db.scalar(select(WebsiteWorkRequest).where(
        WebsiteWorkRequest.id == _id(request_id),
        WebsiteWorkRequest.org_id == org_id,
        WebsiteWorkRequest.created_by == actor).with_for_update())
    if item is None:
        raise WebsiteWorkDenied("Website work request unavailable")
    if item.status == "completed":
        raise WebsiteWorkDenied("Completed work cannot be cancelled; edit or revert the draft")
    if item.status == "queued":
        item.status = "cancelled"
        item.completed_at = now()
        audit(db, org_id, actor, "website.work_cancelled",
              "website_work_request", item.id)
    return item


def _actor(bearer, issuer: str, audience: str, write: bool = False) -> uuid.UUID:
    identity = getattr(bearer, "identity", None)
    scopes = {"mf:read", WRITE_SCOPE} if write else {"mf:read"}
    if (identity is None or identity.issuer != issuer or identity.audience != audience
            or not isinstance(identity.scopes, frozenset)
            or not scopes.issubset(identity.scopes)
            or type(getattr(bearer, "issued_at", None)) is not int):
        raise ConnectorAccessDenied("Verified website OAuth permission is required")
    user_id = linked_subject_user_id(identity.issuer, identity.subject, bearer.issued_at)
    if not isinstance(user_id, uuid.UUID):
        raise ConnectorAccessDenied("This ChatGPT identity link is no longer active")
    return user_id


def _enter(db, user_id: uuid.UUID, org_id: uuid.UUID) -> None:
    user = db.get(User, user_id)
    member = db.scalar(select(Membership).where(
        Membership.user_id == user_id, Membership.org_id == org_id))
    # Work instructions and drafts are owner-controlled even when simply listed.
    # A staff read scope never reveals owner website requests.
    if (user is None or user.disabled_at is not None or member is None
            or member.role not in ("owner", "admin")
            or not can(member.role, "site.edit")):
        raise ConnectorAccessDenied("Website business is unavailable")
    db.set_context(org_id, user_id)
    business = db.get(Organization, org_id)
    if business is None or business.status != "active":
        raise ConnectorAccessDenied("Website business is unavailable")


def list_for_oauth(bearer, *, issuer: str, audience: str,
                   business_id: str) -> dict:
    actor = _actor(bearer, issuer, audience)
    org = _id(business_id)
    with session_scope(user_id=actor) as db:
        _enter(db, actor, org)
        site = sites.get_site(db)
        if site is None:
            return {"business_id": str(org), "website": None, "work_requests": []}
        tasks = db.scalars(select(WebsiteWorkRequest).where(
            WebsiteWorkRequest.org_id == org, WebsiteWorkRequest.site_id == site.id,
            WebsiteWorkRequest.status == "queued",
        ).order_by(WebsiteWorkRequest.created_at.asc()).limit(20)).all()
        return {
            "business_id": str(org),
            "website": {
                "site_id": str(site.id),
                "current_draft_sha256": _hash(site.draft),
                "draft": dict(site.draft),
                "has_published_version": bool(site.published_version_id),
            },
            "work_requests": [
                {"id": str(task.id), "instructions": task.instruction,
                 "expected_draft_sha256": task.expected_draft_sha256,
                 "created_at": task.created_at.isoformat()}
                for task in tasks
            ],
        }


def apply_for_oauth(bearer, *, issuer: str, audience: str,
                    business_id: str, request_id: str,
                    changes: dict) -> dict:
    actor = _actor(bearer, issuer, audience, write=True)
    org = _id(business_id)
    target = _id(request_id)
    patch, patch_hash = _patch(changes)
    with session_scope(user_id=actor) as db:
        _enter(db, actor, org)
        request = db.scalar(select(WebsiteWorkRequest).where(
            WebsiteWorkRequest.id == target,
            WebsiteWorkRequest.org_id == org,
        ).with_for_update())
        if request is None:
            raise WebsiteWorkDenied("Website request unavailable")
        if request.status == "completed":
            if request.applied_payload_sha256 != patch_hash:
                raise WebsiteWorkDenied("This request was completed with different content")
            return {
                "request_id": str(request.id), "status": "completed", "already_exists": True,
                "draft_sha256": request.resulting_draft_sha256, "published": False,
            }
        if request.status != "queued":
            raise WebsiteWorkDenied("Cancelled website work cannot be applied")
        site = db.scalar(select(Site).where(
            Site.id == request.site_id, Site.org_id == org).with_for_update())
        if site is None:
            raise WebsiteWorkDenied("Website not found")
        if _hash(site.draft) != request.expected_draft_sha256:
            raise WebsiteWorkDenied(
                "The website draft changed since this request; owner must review and resubmit")
        merged = dict(site.draft)
        merged.update(patch)
        try:
            validated = sitegen.validate_profile(merged)
        except sitegen.ProfileError:
            raise WebsiteWorkDenied("The proposed website changes were invalid") from None
        sites.update_draft(db, site, actor, validated)
        request.status = "completed"
        request.applied_payload_sha256 = patch_hash
        request.resulting_draft_sha256 = _hash(site.draft)
        request.completed_by = actor
        request.completed_at = now()
        audit(db, org, actor, "website.work_applied",
              "website_work_request", request.id,
              {"site_id": str(site.id), "draft_sha256": request.resulting_draft_sha256})
        return {
            "request_id": str(request.id), "site_id": str(site.id),
            "status": "completed", "already_exists": False,
            "draft_sha256": request.resulting_draft_sha256,
            "published": False, "website_review_url": "/app/website/studio",
        }
