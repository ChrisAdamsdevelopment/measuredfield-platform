"""First-party owner approval for OAuth-created inquiry reply drafts.

A ChatGPT/MCP tool CANNOT invoke these routes with an OAuth bearer. This is a
separate browser-session and CSRF-confirmed owner decision. Approving stores
review evidence only; it never sends an email or changes an inquiry status.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select

from ..db import TenantSession, get_db
from ..deps import Principal, back, member, member_post, parse_uuid
from ..models import Lead, McpActionDraft, now
from ..services.audit import audit
from .workspace import _page

router = APIRouter()


@router.get("/app/mcp/approvals")
def action_approvals(request: Request, db: TenantSession = Depends(get_db),
                     p: Principal = Depends(member("read"))):
    if p.role != "owner":
        raise HTTPException(403, "Only a business owner can review AI drafts.")
    entries = db.execute(
        select(McpActionDraft, Lead)
        .join(Lead, McpActionDraft.lead_id == Lead.id)
        .where(McpActionDraft.org_id == p.org.id, Lead.org_id == p.org.id)
        .order_by(McpActionDraft.created_at.desc(), McpActionDraft.id.desc())
        .limit(100)
    ).all()
    return _page(request, db, p, "app/mcp_approvals.html", {"entries": entries})


@router.post("/app/mcp/approvals/{draft_id}/decide")
def decide_draft(draft_id: str, decision: str = Form(""), confirm: str = Form(""),
                 db: TenantSession = Depends(get_db),
                 p: Principal = Depends(member_post("leads.write"))):
    if p.role != "owner":
        raise HTTPException(403, "Only the owner can approve an OAuth reply draft.")
    if confirm != "yes" or decision not in ("approved", "declined"):
        raise HTTPException(422, "Explicit review decision and confirmation required.")
    draft = db.scalar(select(McpActionDraft).where(
        McpActionDraft.id == parse_uuid(draft_id),
        McpActionDraft.org_id == p.org.id).with_for_update())
    if draft is None:
        raise HTTPException(404, "Draft not found")
    if draft.status == decision:
        return back("/app/mcp/approvals", ok="Already reviewed. No new action was taken.")
    if draft.status != "pending_review":
        raise HTTPException(409, "This draft has already been reviewed and cannot be changed.")
    lead = db.get(Lead, draft.lead_id)
    if lead is None or lead.org_id != p.org.id:
        raise HTTPException(404, "Inquiry no longer available.")
    draft.status, draft.reviewed_by, draft.reviewed_at = decision, p.user.id, now()
    audit(db, p.org.id, p.user.id, "mcp.reply_draft_" + decision,
          "mcp_action_draft", draft.id, {"inquiry_id": str(lead.id)})
    notice = ("Draft approved for manual sending; no email has been sent."
              if decision == "approved" else
              "Draft declined. No customer was contacted.")
    return back("/app/mcp/approvals", ok=notice)
