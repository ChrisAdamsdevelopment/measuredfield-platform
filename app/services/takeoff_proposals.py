"""Snapshot-bound, inert correction drafts; no automatic takeoff mutation."""
from __future__ import annotations

import hashlib
import json
import math
import uuid

from sqlalchemy import func, select

from app.db import session_scope
from app.mcp_connector.gateway import ConnectorAccessDenied
from app.mcp_plugin.links import linked_subject_user_id
from app.models import Document, Membership, Organization, User
from app.services import takeoff_share as share
from app.services.audit import audit

CONTENT_TYPE = "application/vnd.measuredfield.takeoff-draft+json"
MAX_DRAFTS = 10
TARGET_SECTIONS = ("drawing_register", "scope_items", "measurements",
                   "field_entered_quantities", "material_lines", "price_research_queue")


def _prefix(snapshot_id):
    return "takeoff-draft-" + str(snapshot_id) + "-"


def _drafts(db, org_id, user_id, snapshot_id):
    return select(Document).where(Document.org_id == org_id, Document.uploaded_by == user_id,
        Document.content_type == CONTENT_TYPE, Document.filename.startswith(_prefix(snapshot_id)))


def _decode(doc):
    if doc.size > 8192 or len(doc.data) != doc.size or hashlib.sha256(doc.data).hexdigest() != doc.sha256:
        raise share.ShareError("Draft integrity check failed")
    return {**json.loads(doc.data), "draft_id": str(doc.id), "created_at": doc.created_at.isoformat()}


def _proposal(snapshot, section, target_id, kind, quantity, rationale):
    if section not in TARGET_SECTIONS or not isinstance(target_id, str) or not share.ID.fullmatch(target_id):
        raise share.ShareError("A source-linked target is required")
    if not isinstance(rationale, str) or not 1 <= len(rationale.strip()) <= 1200 or any(ord(c)<32 and c not in "\n\t" for c in rationale):
        raise share.ShareError("A bounded review rationale is required")
    key = "line_id" if section in ("material_lines", "price_research_queue") else "id"
    matching = [r for r in snapshot[section] if r.get(key) == target_id]
    if len(matching) != 1:
        raise share.ShareError("Target is missing or ambiguous in the shared snapshot")
    target = matching[0]
    out = {"kind":kind, "section":section, "target_id":target_id, "rationale":rationale.strip(),
           "status":"draft_only", "source":target}
    if kind == "field_quantity":
        if section != "field_entered_quantities" or target.get("source") != "field_entered_unverified":
            raise share.ShareError("Only contractor-entered field quantities can be proposed for correction")
        old, unit = target.get("quantity"), target.get("unit")
        if (type(old) not in (int,float) or not math.isfinite(old) or old <= 0 or
                type(quantity) not in (int,float) or not 0 < quantity <= 1000000 or
                not math.isfinite(quantity) or quantity == old or
                unit not in ("linear feet","items","square feet") or
                (unit == "items" and int(quantity) != quantity)):
            raise share.ShareError("Proposed quantity must be a valid different quantity in the original unit")
        out.update(expected_quantity=old, proposed_quantity=quantity, unit=unit)
    elif kind == "review_note":
        if quantity is not None:
            raise share.ShareError("Review notes cannot set a quantity")
    else:
        raise share.ShareError("Unsupported draft kind")
    return out


def create_for_oauth(bearer, *, issuer, audience, read_scope, write_scope, business_id,
                     snapshot_id, section, target_id, kind, quantity, rationale):
    identity = getattr(bearer, "identity", None)
    if (identity is None or identity.issuer != issuer or identity.audience != audience or
            not {read_scope,write_scope}.issubset(identity.scopes) or type(bearer.issued_at) is not int):
        raise ConnectorAccessDenied("Takeoff draft write permission is required")
    user_id = linked_subject_user_id(identity.issuer, identity.subject, bearer.issued_at)
    if not isinstance(user_id, uuid.UUID):
        raise ConnectorAccessDenied("Takeoff draft is unavailable")
    try:
        org_id = uuid.UUID(business_id)
        sid = uuid.UUID(snapshot_id)
    except (ValueError,TypeError,AttributeError):
        raise ConnectorAccessDenied("Takeoff draft is unavailable") from None
    if not share.enabled_for(org_id):
        raise ConnectorAccessDenied("Takeoff draft is unavailable")
    with session_scope(user_id=user_id) as db:
        user = db.get(User,user_id)
        member = db.scalar(select(Membership).where(Membership.org_id==org_id,Membership.user_id==user_id))
        if user is None or user.disabled_at is not None or member is None or member.role!="owner":
            raise ConnectorAccessDenied("Takeoff draft is unavailable")
        db.set_context(org_id,user_id)
        member = db.scalar(select(Membership).where(Membership.org_id==org_id,Membership.user_id==user_id).with_for_update())
        org = db.scalar(select(Organization).where(Organization.id==org_id).with_for_update())
        if member is None or member.role!="owner" or org is None or org.status!="active":
            raise ConnectorAccessDenied("Takeoff draft is unavailable")
        doc = share.find(db,org_id,user_id,sid)
        if doc is None:
            raise ConnectorAccessDenied("Takeoff draft is unavailable")
        snapshot = share.load_snapshot(doc)
        if snapshot.get("_share",{}).get("allow_drafts") is not True:
            raise ConnectorAccessDenied("Snapshot owner did not authorize correction drafts")
        proposal = _proposal(snapshot,section,target_id,kind,quantity,rationale)
        proposal.update(snapshot_id=str(sid), snapshot_sha256=doc.sha256)
        raw = json.dumps(proposal,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(",",":")).encode()
        if len(raw)>8192:
            raise share.ShareError("Draft exceeds supported size")
        digest=hashlib.sha256(raw).hexdigest()
        existing=db.scalar(_drafts(db,org_id,user_id,sid).where(Document.sha256==digest).limit(1))
        if existing:
            return {"draft_id":str(existing.id),"snapshot_id":str(sid),"status":"draft_only","already_exists":True}
        count=db.scalar(select(func.count()).select_from(_drafts(db,org_id,user_id,sid).subquery()))
        if count>=MAX_DRAFTS:
            raise share.ShareError("Snapshot draft limit reached")
        entry=Document(org_id=org_id,uploaded_by=user_id,filename=_prefix(sid)+digest[:16]+".json",
            content_type=CONTENT_TYPE,size=len(raw),sha256=digest,data=raw)
        db.add(entry); db.flush()
        audit(db,org_id,user_id,"takeoff.draft_created","document",entry.id,{"snapshot_id":str(sid),"kind":kind})
        return {"draft_id":str(entry.id),"snapshot_id":str(sid),"status":"draft_only","already_exists":False}


def list_for_owner(db,org_id,user_id,snapshot_id):
    doc=share.find(db,org_id,user_id,snapshot_id)
    if doc is None:
        return None
    snapshot=share.load_snapshot(doc)
    meta=snapshot.get("_share",{})
    if meta.get("allow_drafts") is not True:
        return {"drafts":[],"drafts_allowed":False}
    drafts=db.scalars(_drafts(db,org_id,user_id,doc.id).order_by(Document.created_at.desc()).limit(MAX_DRAFTS)).all()
    return {"drafts_allowed":True,"snapshot_id":str(doc.id),"snapshot_sha256":doc.sha256,
        "project_ref":meta["project_ref"],"client_fingerprint":meta["client_fingerprint"],
        "drafts":[_decode(d) for d in drafts]}


def remove_for_snapshot(db,org_id,user_id,snapshot_id):
    for doc in db.scalars(_drafts(db,org_id,user_id,snapshot_id)).all():
        db.delete(doc)
