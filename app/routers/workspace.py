"""The private workspace (/app) and its JSON API (/api/v1).

Every handler gets its organization from ``Principal`` (session + membership). Ids in the
URL are fetched with ``_get`` inside that tenant's row-level-security context: a row from
another organization is invisible, so the answer is 404, the same as for a made-up id.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, back, member, member_api, member_post, parse_uuid, render
from ..models import (AssessmentReport, AuditEvent, Customer, DataRequest, Document, Improvement, Lead,
                      LeadNote, LessonCandidate, Membership, MemoryItem, Project, Proposal, SiteVersion,
                      User, now)
from ..security import ROLES, digest, new_token, valid_email
from ..services import growth, jobs, lead_metrics, learning, orders, sitegen, sites, tenant_data
from ..services.audit import audit
from .auth import list_orgs

router = APIRouter()

LEAD_STATUSES = ("new", "contacted", "qualified", "won", "lost", "spam")
PROJECT_STATUSES = ("open", "scheduled", "in_progress", "done", "cancelled")
IMPROVEMENT_STATUSES = ("proposed", "approved", "implemented", "dismissed")
UPLOAD_TYPES = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _get(db: TenantSession, model, raw_id: str):
    row = db.get(model, parse_uuid(raw_id))
    if row is None:
        raise HTTPException(404, "Not found")
    return row


def _page(request: Request, db: TenantSession, p: Principal, name: str, ctx: dict | None = None, status_code: int = 200):
    base = {"org": p.org, "role": p.role, "can": p.can, "orgs": list_orgs(db, p.user)}
    base.update(ctx or {})
    return render(request, name, base, status_code=status_code)


def _members(db: TenantSession) -> list[tuple[Membership, User]]:
    return list(db.execute(select(Membership, User).join(User, User.id == Membership.user_id)
                           .order_by(Membership.created_at)).all())


# ------------------------------------------------------------------------- dashboard

@router.get("/app")
def dashboard(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    site = sites.get_site(db)
    today = date.today()
    counts = dict(db.execute(select(Lead.status, func.count()).group_by(Lead.status)).all())
    due = db.scalar(select(func.count()).select_from(Lead).where(
        Lead.follow_up_on <= today, Lead.status.in_(("new", "contacted", "qualified")))) or 0
    unanswered = db.scalar(select(func.count()).select_from(Lead).where(
        Lead.status == "new", Lead.first_response_at.is_(None))) or 0
    improvements = dict(db.execute(select(Improvement.status, func.count()).group_by(Improvement.status)).all())
    recent = db.scalars(select(Lead).order_by(Lead.created_at.desc()).limit(5)).all()
    return _page(request, db, p, "app/dashboard.html", {
        "site": site, "lead_counts": counts, "leads_total": sum(counts.values()), "due": due,
        "unanswered": unanswered, "improvements": improvements, "recent": recent,
        "measured": sites.measured_totals(db, site), "site_host": sites.subdomain_host(site.slug) if site else ""})


# ------------------------------------------------------------------------- inquiries

@router.get("/app/inquiries")
def inquiries(request: Request, status: str = "", db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    statement = select(Lead).order_by(Lead.created_at.desc()).limit(200)
    if status in LEAD_STATUSES:
        statement = statement.where(Lead.status == status)
    return _page(request, db, p, "app/inquiries.html", {
        "leads": db.scalars(statement).all(), "status": status, "statuses": LEAD_STATUSES, "today": date.today()})


@router.get("/app/inquiries/{lead_id}")
def inquiry(lead_id: str, request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    lead = _get(db, Lead, lead_id)
    notes = db.execute(select(LeadNote, User.name).outerjoin(User, User.id == LeadNote.author_id)
                       .where(LeadNote.lead_id == lead.id).order_by(LeadNote.created_at)).all()
    return _page(request, db, p, "app/inquiry.html", {
        "lead": lead, "notes": notes, "members": _members(db), "statuses": LEAD_STATUSES,
        "timings": sitegen.TIMINGS})


@router.post("/app/inquiries/{lead_id}/update")
def inquiry_update(lead_id: str, status: str = Form(""), assigned_to: str = Form(""), follow_up_on: str = Form(""),
                   db: TenantSession = Depends(get_db), p: Principal = Depends(member_post("leads.write"))):
    lead = _get(db, Lead, lead_id)
    changes = {}
    if status in LEAD_STATUSES and status != lead.status:
        changes["status"] = [lead.status, status]
        lead.status = status
        if status in ("contacted", "qualified", "won") and lead.first_response_at is None:
            lead.first_response_at = now()
    if assigned_to:
        assignee = parse_uuid(assigned_to)
        # The assignee must be a member of THIS organization.
        if not db.scalar(select(Membership.id).where(Membership.user_id == assignee, Membership.org_id == p.org.id)):
            raise HTTPException(422, "Choose a team member.")
        if lead.assigned_to != assignee:
            lead.assigned_to = assignee
            changes["assigned"] = True
    elif lead.assigned_to is not None:
        lead.assigned_to = None
        changes["assigned"] = False
    if follow_up_on:
        try:
            when = date.fromisoformat(follow_up_on)
        except ValueError:
            raise HTTPException(422, "Enter a valid follow-up date.") from None
        if when != lead.follow_up_on:
            lead.follow_up_on = when
            changes["follow_up_on"] = when.isoformat()
    elif lead.follow_up_on is not None:
        lead.follow_up_on = None
        changes["follow_up_on"] = None
    if changes:
        audit(db, p.org.id, p.user.id, "lead.updated", "lead", lead.id, changes)
    return back(f"/app/inquiries/{lead.id}", ok="Saved")


@router.post("/app/inquiries/{lead_id}/note")
def inquiry_note(lead_id: str, body: str = Form(""), db: TenantSession = Depends(get_db),
                 p: Principal = Depends(member_post("leads.write"))):
    lead = _get(db, Lead, lead_id)
    body = body.strip()
    if not 1 <= len(body) <= 4000:
        return back(f"/app/inquiries/{lead.id}", err="Write a note of up to 4000 characters.")
    db.add(LeadNote(org_id=p.org.id, lead_id=lead.id, author_id=p.user.id, body=body))
    audit(db, p.org.id, p.user.id, "lead.note_added", "lead", lead.id)
    return back(f"/app/inquiries/{lead.id}", ok="Note added")


@router.post("/app/inquiries/{lead_id}/convert")
def inquiry_convert(lead_id: str, db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("customers.write"))):
    lead = _get(db, Lead, lead_id)
    if lead.customer_id is None:
        customer = Customer(org_id=p.org.id, name=lead.name, email=lead.email, phone=lead.phone)
        db.add(customer)
        db.flush()
        db.add(Project(org_id=p.org.id, customer_id=customer.id, title=lead.service or "New project",
                       notes="Created from a website inquiry."))
        lead.customer_id = customer.id
        if lead.status == "new":
            lead.status = "qualified"
        audit(db, p.org.id, p.user.id, "lead.converted", "customer", customer.id, {"lead_id": str(lead.id)})
    return back(f"/app/customers/{lead.customer_id}", ok="Customer and project created")


# --------------------------------------------------------------------------- website

def _profile_form(form) -> dict:
    return {k: (form.get(k) or "") for k in ("name", "city", "industry", "color", "email", "phone", "about",
                                             "headline", "intro", "response_window", "services", "structured_intake")}


@router.get("/app/website")
def website(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    site = sites.get_site(db)
    versions = db.scalars(select(SiteVersion).where(SiteVersion.site_id == site.id)
                          .order_by(SiteVersion.number.desc())).all() if site else []
    draft = dict(site.draft) if site else {"name": p.org.name, "industry": p.org.industry, "color": "ocean",
                                           "response_window": "no_promise", "services": []}
    dirty = bool(site and versions and versions[0].content != site.draft) or bool(site and not versions)
    return _page(request, db, p, "app/website.html", {
        "site": site, "versions": versions, "draft": draft, "services_text": sitegen.services_as_text(draft),
        "colors": sitegen.COLORS, "industries": sitegen.INDUSTRIES, "windows": sitegen.RESPONSE_WINDOWS,
        "suggested_slug": sitegen.slugify(p.org.name), "base_domain": get_settings().sites_base_domain,
        "hosts": sites.list_hosts(db, site) if site else [], "dirty": dirty})


@router.post("/app/website/save")
async def website_save(request: Request, db: TenantSession = Depends(get_db),
                       p: Principal = Depends(member_post("site.edit"))):
    form = await request.form()
    site = sites.get_site(db)
    try:
        if site is None:
            sites.create_site(db, p.org.id, p.user.id, _profile_form(form), str(form.get("slug") or ""))
        else:
            sites.update_draft(db, site, p.user.id, _profile_form(form))
    except (sitegen.ProfileError, sites.SiteError) as exc:
        return back("/app/website", err=str(exc))
    return back("/app/website", ok="Draft saved. Preview it, then save a version when you're happy.")


@router.get("/app/website/preview/{asset:path}")
def website_preview(asset: str, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    site = sites.get_site(db)
    if site is None:
        raise HTTPException(404, "Not found")
    html, css = sitegen.render(site.draft, asset_base="/app/website/preview/")
    if asset in ("", "index.html"):
        return Response(html, media_type="text/html; charset=utf-8")
    if asset == "style.css":
        return Response(css, media_type="text/css; charset=utf-8")
    if asset == "site.js":
        return Response(sitegen.SITE_JS, media_type="text/javascript; charset=utf-8")
    raise HTTPException(404, "Not found")


@router.post("/app/website/preview/api/{kind}")
def website_preview_api(kind: str, p: Principal = Depends(member())):
    """The preview form never stores anything; it tells the page so."""
    if kind == "lead":
        return JSONResponse({"ok": True, "preview": True}, status_code=200)
    return Response(status_code=204)


@router.get("/app/website/versions/{version_id}/view/{asset:path}")
def website_version_view(version_id: str, asset: str, db: TenantSession = Depends(get_db),
                         p: Principal = Depends(member())):
    version = _get(db, SiteVersion, version_id)
    if asset in ("", "index.html"):
        # Stored pages reference root-relative assets; rewrite for this nested path.
        html = version.html.replace('href="/style.css"', 'href="style.css"') \
            .replace('src="/site.js"', "").replace('action="/api/lead"', 'action="#"')
        return Response(html, media_type="text/html; charset=utf-8")
    if asset == "style.css":
        return Response(version.css, media_type="text/css; charset=utf-8")
    raise HTTPException(404, "Not found")


@router.post("/app/website/version")
def website_version(note: str = Form(""), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("site.edit"))):
    site = sites.get_site(db)
    if site is None:
        return back("/app/website", err="Save your website details first.")
    version = sites.save_version(db, site, p.user.id, note.strip())
    return back("/app/website", ok=f"Version {version.number} saved. An owner or admin can approve and publish it.")


@router.post("/app/website/versions/{version_id}/approve")
def website_approve(version_id: str, confirm_approve: str = Form(""), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("site.publish"))):
    version = _get(db, SiteVersion, version_id)
    if confirm_approve != "yes":
        raise HTTPException(403, "Confirm approval of this version before continuing")
    sites.approve_version(db, version, p.user.id)
    return back("/app/website", ok=f"Version {version.number} approved")


@router.post("/app/website/versions/{version_id}/publish")
def website_publish(version_id: str, confirm_publish: str = Form(""), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("site.publish"))):
    version = _get(db, SiteVersion, version_id)
    site = sites.get_site(db)
    if confirm_publish != "yes":
        raise HTTPException(403, "Confirm publishing this version before continuing")
    current = db.get(SiteVersion, site.published_version_id) if site and site.published_version_id else None
    try:
        sites.publish(db, site, version, p.user.id, rollback=bool(current and current.number > version.number))
    except sites.SiteError as exc:
        return back("/app/website", err=str(exc))
    improvement = db.scalar(select(Improvement).where(Improvement.capability == "website",
                                                      Improvement.status.in_(("proposed", "approved"))))
    if improvement is not None:
        improvement.status, improvement.implemented_at = "implemented", now()
        improvement.evidence_status = "system_recorded"
        improvement.evidence_note = (f"Website version {version.number} was published on "
                                     f"{now().date().isoformat()}. Visitor and inquiry counts are measured from this date.")
    return back("/app/website", ok=f"Version {version.number} is now live")


@router.post("/app/website/unpublish")
def website_unpublish(confirm_unpublish: str = Form(""), db: TenantSession = Depends(get_db),
                      p: Principal = Depends(member_post("site.publish"))):
    site = sites.get_site(db)
    if confirm_unpublish != "yes":
        raise HTTPException(403, "Confirm taking this website offline before continuing")
    if site is not None:
        sites.unpublish(db, site, p.user.id)
    return back("/app/website", ok="Your website is offline")


@router.post("/app/website/domains")
def website_domain(hostname: str = Form(""), db: TenantSession = Depends(get_db),
                   p: Principal = Depends(member_post("site.publish"))):
    site = sites.get_site(db)
    if site is None:
        return back("/app/website", err="Create your website first.")
    try:
        sites.add_custom_domain(db, site, p.user.id, hostname)
    except sites.SiteError as exc:
        return back("/app/website", err=str(exc))
    return back("/app/website", ok="Domain requested. It goes live after we verify DNS with you.")


# ---------------------------------------------------------- assessment + improvements

@router.get("/app/improvements")
def improvements(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    site = sites.get_site(db)
    return _page(request, db, p, "app/improvements.html", {
        "report": db.scalar(select(AssessmentReport).order_by(AssessmentReport.created_at.desc())),
        "items": db.scalars(select(Improvement).order_by(Improvement.created_at)).all(),
        "statuses": IMPROVEMENT_STATUSES, "measured": sites.measured_totals(db, site), "site": site})


@router.post("/app/improvements")
def improvement_add(title: str = Form(""), detail: str = Form(""), metric: str = Form(""),
                    db: TenantSession = Depends(get_db), p: Principal = Depends(member_post("improvements.write"))):
    title = title.strip()
    if not 3 <= len(title) <= 200:
        return back("/app/improvements", err="Give the improvement a short title.")
    item = Improvement(org_id=p.org.id, title=title, detail=detail.strip()[:2000], metric=metric.strip()[:200],
                       source="team", evidence_status="unknown",
                       evidence_note="Added by your team. No evidence recorded yet.")
    db.add(item)
    db.flush()
    audit(db, p.org.id, p.user.id, "improvement.added", "improvement", item.id)
    return back("/app/improvements", ok="Added")


@router.post("/app/improvements/{item_id}/status")
def improvement_status(item_id: str, status: str = Form(""), evidence_note: str = Form(""),
                       db: TenantSession = Depends(get_db), p: Principal = Depends(member_post("improvements.write"))):
    item = _get(db, Improvement, item_id)
    if status not in IMPROVEMENT_STATUSES:
        raise HTTPException(422, "Unknown status")
    previous = item.status
    item.status = status
    if status == "implemented" and item.implemented_at is None:
        item.implemented_at = now()
    if evidence_note.strip():
        # A typed note is the team's own statement, and is labelled that way.
        item.evidence_note = evidence_note.strip()[:2000]
        if item.evidence_status in ("unknown", "owner_stated"):
            item.evidence_status = "owner_stated"
    audit(db, p.org.id, p.user.id, "improvement.status_changed", "improvement", item.id, {"from": previous, "to": status})
    return back("/app/improvements", ok="Updated")


@router.get("/app/insights")
def insights(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    site = sites.get_site(db)
    measured = sites.measured_totals(db, site)
    observed_at = now()
    from_date = observed_at - timedelta(days=30)
    # Read only the timestamps/status needed for first-party analysis; tenant id
    # is explicitly constrained in addition to PostgreSQL row-level security.
    observations = db.execute(select(Lead.created_at, Lead.first_response_at, Lead.status).where(
        Lead.org_id == p.org.id, Lead.created_at >= from_date))
    response_metrics = lead_metrics.summarize_responses(observations, as_of=observed_at)
    return _page(request, db, p, "app/insights.html", {
        "measured": measured, "site": site,
        "leads_total": response_metrics["leads_total"],
        "leads_responded": response_metrics["responses_recorded"],
        "response_metrics": response_metrics,
        "report": growth.review_for_org(p.org.name, measured), "node": growth.node_available()})


# ---------------------------------------------------- customers, projects, proposals

@router.get("/app/customers")
def customers(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    return _page(request, db, p, "app/customers.html",
                 {"customers": db.scalars(select(Customer).order_by(Customer.created_at.desc()).limit(300)).all()})


@router.post("/app/customers")
def customer_add(name: str = Form(""), email: str = Form(""), phone: str = Form(""),
                 db: TenantSession = Depends(get_db), p: Principal = Depends(member_post("customers.write"))):
    name = " ".join(name.split())
    if not 2 <= len(name) <= 120 or (email and not valid_email(email.strip().lower())):
        return back("/app/customers", err="Enter a name and, if you add one, a valid email.")
    customer = Customer(org_id=p.org.id, name=name, email=email.strip().lower()[:254], phone=phone.strip()[:40])
    db.add(customer)
    db.flush()
    audit(db, p.org.id, p.user.id, "customer.created", "customer", customer.id)
    return back(f"/app/customers/{customer.id}", ok="Customer added")


@router.get("/app/customers/{customer_id}")
def customer(customer_id: str, request: Request, link: str = "", db: TenantSession = Depends(get_db),
             p: Principal = Depends(member())):
    row = _get(db, Customer, customer_id)
    return _page(request, db, p, "app/customer.html", {
        "customer": row, "project_statuses": PROJECT_STATUSES,
        "projects": db.scalars(select(Project).where(Project.customer_id == row.id).order_by(Project.created_at)).all(),
        "proposals": db.scalars(select(Proposal).where(Proposal.customer_id == row.id).order_by(Proposal.created_at)).all()})


@router.post("/app/customers/{customer_id}/notes")
def customer_notes(customer_id: str, notes: str = Form(""), db: TenantSession = Depends(get_db),
                   p: Principal = Depends(member_post("customers.write"))):
    row = _get(db, Customer, customer_id)
    row.notes = notes.strip()[:8000]
    audit(db, p.org.id, p.user.id, "customer.notes_updated", "customer", row.id)
    return back(f"/app/customers/{row.id}", ok="Saved")


@router.post("/app/customers/{customer_id}/projects")
def project_add(customer_id: str, title: str = Form(""), db: TenantSession = Depends(get_db),
                p: Principal = Depends(member_post("customers.write"))):
    row = _get(db, Customer, customer_id)
    if not 2 <= len(title.strip()) <= 160:
        return back(f"/app/customers/{row.id}", err="Give the project a title.")
    project = Project(org_id=p.org.id, customer_id=row.id, title=title.strip())
    db.add(project)
    db.flush()
    audit(db, p.org.id, p.user.id, "project.created", "project", project.id)
    return back(f"/app/customers/{row.id}", ok="Project added")


@router.post("/app/projects/{project_id}/status")
def project_status(project_id: str, status: str = Form(""), db: TenantSession = Depends(get_db),
                   p: Principal = Depends(member_post("customers.write"))):
    project = _get(db, Project, project_id)
    if status not in PROJECT_STATUSES:
        raise HTTPException(422, "Unknown status")
    audit(db, p.org.id, p.user.id, "project.status_changed", "project", project.id, {"from": project.status, "to": status})
    project.status = status
    return back(f"/app/customers/{project.customer_id}", ok="Updated")


def parse_items(raw: str) -> tuple[list[dict], int]:
    """Lines of ``Description | quantity | unit price``. Money is kept in integer cents."""
    items, total = [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or not parts[0]:
            raise ValueError("Each line needs: description | quantity | unit price")
        try:
            quantity = float(parts[1])
            price = round(float(parts[2].replace("$", "").replace(",", "")) * 100)
        except ValueError:
            raise ValueError("Quantity and price must be numbers.") from None
        if not (0 < quantity <= 100000 and 0 <= price <= 100_000_000):
            raise ValueError("Quantity or price is out of range.")
        line_total = round(quantity * price)
        items.append({"description": parts[0][:200], "quantity": quantity, "unit_price_cents": price, "total_cents": line_total})
        total += line_total
    if not 1 <= len(items) <= 40:
        raise ValueError("Add between 1 and 40 lines.")
    return items, total


@router.post("/app/customers/{customer_id}/proposals")
def proposal_add(customer_id: str, title: str = Form(""), scope: str = Form(""), items: str = Form(""),
                 db: TenantSession = Depends(get_db), p: Principal = Depends(member_post("customers.write"))):
    row = _get(db, Customer, customer_id)
    try:
        parsed, total = parse_items(items)
    except ValueError as exc:
        return back(f"/app/customers/{row.id}", err=str(exc))
    if not 2 <= len(title.strip()) <= 160:
        return back(f"/app/customers/{row.id}", err="Give the proposal a title.")
    proposal = Proposal(org_id=p.org.id, customer_id=row.id, title=title.strip(), scope=scope.strip()[:4000],
                        items=parsed, total_cents=total)
    db.add(proposal)
    db.flush()
    audit(db, p.org.id, p.user.id, "proposal.created", "proposal", proposal.id, {"total_cents": total})
    return back(f"/app/customers/{row.id}", ok="Proposal drafted")


@router.post("/app/proposals/{proposal_id}/send")
def proposal_send(proposal_id: str, request: Request, db: TenantSession = Depends(get_db),
                  p: Principal = Depends(member_post("customers.write"))):
    """Create the customer's approval link. The link is shown once; we do not email it,
    so nothing claims a message was delivered. The owner sends it however they choose."""
    proposal = _get(db, Proposal, proposal_id)
    if proposal.status not in ("draft", "sent"):
        return back(f"/app/customers/{proposal.customer_id}", err="This proposal has already been answered.")
    token = p.org.id.hex + "." + new_token()
    proposal.token_hash, proposal.status = digest(token), "sent"
    audit(db, p.org.id, p.user.id, "proposal.link_created", "proposal", proposal.id, {"revision": proposal.revision})
    row = db.get(Customer, proposal.customer_id)
    return _page(request, db, p, "app/proposal_link.html", {
        "proposal": proposal, "customer": row, "link": f"{get_settings().public_base_url}/p/{token}"})


# ------------------------------------------------------------------------- documents

@router.get("/app/documents")
def documents(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    return _page(request, db, p, "app/documents.html", {
        "documents": db.scalars(select(Document).order_by(Document.created_at.desc()).limit(300)).all(),
        "max_mb": get_settings().max_upload_bytes // (1024 * 1024), "types": sorted(UPLOAD_TYPES)})


@router.post("/app/documents")
def document_upload(file: UploadFile = File(...), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("documents.write"))):
    settings = get_settings()
    name = re.sub(r"[^A-Za-z0-9 ._()-]", "_", (file.filename or "file").replace("\\", "/").split("/")[-1])[:200] or "file"
    extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in UPLOAD_TYPES:
        return back("/app/documents", err="That file type isn't supported.")
    data = file.file.read(settings.max_upload_bytes + 1)
    if not data or len(data) > settings.max_upload_bytes:
        return back("/app/documents", err="The file is empty or larger than the upload limit.")
    document = Document(org_id=p.org.id, filename=name, content_type=UPLOAD_TYPES[extension], size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), data=data, uploaded_by=p.user.id)
    db.add(document)
    db.flush()
    chunks = 0
    if extension in (".txt", ".md"):
        chunks = learning.index_document(db, document, data.decode("utf-8", errors="replace"))
    audit(db, p.org.id, p.user.id, "document.uploaded", "document", document.id, {"size": len(data), "chunks": chunks})
    return back("/app/documents", ok="Uploaded")


@router.get("/app/documents/{document_id}/download")
def document_download(document_id: str, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    document = _get(db, Document, document_id)
    audit(db, p.org.id, p.user.id, "document.downloaded", "document", document.id)
    return Response(document.data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{document.filename}"',
        "Content-Security-Policy": "sandbox; default-src 'none'", "X-Content-Type-Options": "nosniff"})


@router.post("/app/documents/{document_id}/delete")
def document_delete(document_id: str, db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("documents.delete"))):
    document = _get(db, Document, document_id)
    audit(db, p.org.id, p.user.id, "document.deleted", "document", document.id, {"sha256": document.sha256})
    db.delete(document)
    return back("/app/documents", ok="Deleted")


# -------------------------------------------------------------------------- learning

@router.get("/app/learning")
def learning_page(request: Request, q: str = "", db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    return _page(request, db, p, "app/learning.html", {
        "q": q[:100], "memory": learning.search_memory(db, q) if q else
        db.scalars(select(MemoryItem).where(MemoryItem.kind == "observation").order_by(MemoryItem.created_at.desc()).limit(20)).all(),
        "candidates": db.scalars(select(LessonCandidate).order_by(LessonCandidate.created_at.desc())).all(),
        "skills": learning.active_skills(db)})


@router.post("/app/learning/observations")
def observation_add(content: str = Form(""), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("memory.write"))):
    try:
        learning.add_observation(db, p.org.id, p.user.id, content)
    except learning.LearningError as exc:
        return back("/app/learning", err=str(exc))
    return back("/app/learning", ok="Saved to your private notes")


@router.post("/app/learning/consent")
def learning_consent(opt_in: str = Form(""), db: TenantSession = Depends(get_db),
                     p: Principal = Depends(member_post("learning.manage"))):
    learning.set_opt_in(db, p.org, p.user.id, opt_in == "yes")
    return back("/app/learning", ok="Sharing preference updated")


@router.post("/app/learning/candidates")
def candidate_add(title: str = Form(""), body: str = Form(""), db: TenantSession = Depends(get_db),
                  p: Principal = Depends(member_post("memory.write"))):
    try:
        learning.create_candidate(db, p.org.id, p.user.id, title, body)
    except learning.LearningError as exc:
        return back("/app/learning", err=str(exc))
    return back("/app/learning", ok="Draft lesson saved. It stays private until an admin submits it.")


@router.post("/app/learning/candidates/{candidate_id}/submit")
def candidate_submit(candidate_id: str, attest: str = Form(""), db: TenantSession = Depends(get_db),
                     p: Principal = Depends(member_post("learning.manage"))):
    candidate = _get(db, LessonCandidate, candidate_id)
    try:
        learning.submit_candidate(db, p.org, candidate, p.user.id, attest_ownership=attest == "yes")
    except learning.LearningError as exc:
        return back("/app/learning", err=str(exc))
    if candidate.status == "blocked":
        return back("/app/learning", err="The privacy check stopped this lesson. See the reasons listed on it.")
    return back("/app/learning", ok="Submitted for review. Nothing is published until a person approves it.")


# ------------------------------------------------------------------------------ team

@router.get("/app/team")
def team(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    return _page(request, db, p, "app/team.html", {"members": _members(db), "roles": ROLES})


@router.post("/app/team/invite")
def team_invite(email: str = Form(""), role: str = Form("staff"), db: TenantSession = Depends(get_db),
                p: Principal = Depends(member_post("team.manage"))):
    email = email.strip().lower()
    if not valid_email(email) or role not in ROLES:
        return back("/app/team", err="Enter a valid email and role.")
    if role == "owner" and not p.can("team.owners"):
        return back("/app/team", err="Only an owner can invite another owner.")
    orders.invite_member(db, p.org, email, role, invited_by=p.user.id)
    return back("/app/team", ok="Invitation created. See Settings for whether email delivery is active.")


def _owner_count(db: TenantSession) -> int:
    return db.scalar(select(func.count()).select_from(Membership).where(Membership.role == "owner")) or 0


@router.post("/app/team/{membership_id}/role")
def team_role(membership_id: str, role: str = Form(""), db: TenantSession = Depends(get_db),
              p: Principal = Depends(member_post("team.manage"))):
    membership = _get(db, Membership, membership_id)
    if role not in ROLES:
        raise HTTPException(422, "Unknown role")
    if (membership.role == "owner" or role == "owner") and not p.can("team.owners"):
        return back("/app/team", err="Only an owner can change owner access.")
    if membership.role == "owner" and role != "owner" and _owner_count(db) <= 1:
        return back("/app/team", err="A workspace needs at least one owner.")
    audit(db, p.org.id, p.user.id, "team.role_changed", "user", membership.user_id, {"from": membership.role, "to": role})
    membership.role = role
    return back("/app/team", ok="Role updated")


@router.post("/app/team/{membership_id}/remove")
def team_remove(membership_id: str, db: TenantSession = Depends(get_db),
                p: Principal = Depends(member_post("team.manage"))):
    membership = _get(db, Membership, membership_id)
    if membership.role == "owner" and (not p.can("team.owners") or _owner_count(db) <= 1):
        return back("/app/team", err="You can't remove the only owner.")
    audit(db, p.org.id, p.user.id, "team.removed", "user", membership.user_id, {"role": membership.role})
    db.delete(membership)
    return back("/app/team", ok="Removed")


# -------------------------------------------------------------------------- settings

@router.get("/app/settings")
def settings_page(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    settings = get_settings()
    return _page(request, db, p, "app/settings.html", {
        "billing": (p.org.settings or {}).get("billing", {}),
        "deletion": db.scalar(select(DataRequest).where(DataRequest.kind == "delete", DataRequest.status == "requested")),
        "email_live": settings.email_provider != "none", "payments_live": settings.payment_provider != "none",
        "ai_live": settings.ai_provider != "none", "grace_hours": settings.deletion_grace_hours})


@router.post("/app/settings/profile")
def settings_profile(name: str = Form(""), industry: str = Form("other"), db: TenantSession = Depends(get_db),
                     p: Principal = Depends(member_post("settings.write"))):
    name = " ".join(name.split())
    if not 2 <= len(name) <= 120 or industry not in sitegen.INDUSTRIES:
        return back("/app/settings", err="Enter a business name and choose a business type.")
    p.org.name, p.org.industry = name, industry
    audit(db, p.org.id, p.user.id, "org.profile_updated", "organization", p.org.id)
    return back("/app/settings", ok="Saved")


@router.get("/app/settings/export")
def settings_export(db: TenantSession = Depends(get_db), p: Principal = Depends(member("data.manage"))):
    bundle = tenant_data.export_org(db, p.org.id)
    audit(db, p.org.id, p.user.id, "data.exported", "organization", p.org.id)
    return Response(json.dumps(bundle, indent=1), media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="workspace-export.json"'})


@router.post("/app/settings/delete")
def settings_delete(confirm: str = Form(""), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(member_post("data.manage"))):
    if confirm.strip() != p.org.name:
        return back("/app/settings", err="Type the business name exactly to confirm.")
    if p.org.status == "deletion_pending":
        return back("/app/settings", err="Deletion is already scheduled.")
    tenant_data.request_deletion(db, p.org, p.user.id)
    return back("/app/settings", ok="Deletion scheduled. You can cancel it until it runs.")


@router.post("/app/settings/delete/cancel")
def settings_delete_cancel(db: TenantSession = Depends(get_db), p: Principal = Depends(member_post("data.manage"))):
    tenant_data.cancel_deletion(db, p.org, p.user.id)
    return back("/app/settings", ok="Deletion cancelled")


@router.get("/app/activity")
def activity(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(member())):
    rows = db.execute(select(AuditEvent, User.name).outerjoin(User, User.id == AuditEvent.actor_user_id)
                      .order_by(AuditEvent.created_at.desc()).limit(200)).all()
    return _page(request, db, p, "app/activity.html", {"events": rows})


# -------------------------------------------------------------------------- JSON API

def _lead_json(lead: Lead) -> dict:
    return {"id": str(lead.id), "name": lead.name, "email": lead.email, "phone": lead.phone,
            "service": lead.service, "timing": lead.timing, "message": lead.message, "status": lead.status,
            "follow_up_on": lead.follow_up_on.isoformat() if lead.follow_up_on else None,
            "created_at": lead.created_at.isoformat()}


@router.get("/api/v1/me")
def api_me(p: Principal = Depends(member_api("read"))):
    return {"user": {"id": str(p.user.id), "email": p.user.email, "name": p.user.name},
            "organization": {"id": str(p.org.id), "name": p.org.name}, "role": p.role}


@router.get("/api/v1/leads")
def api_leads(db: TenantSession = Depends(get_db), p: Principal = Depends(member_api("read"))):
    return {"leads": [_lead_json(row) for row in db.scalars(select(Lead).order_by(Lead.created_at.desc()).limit(200))]}


@router.get("/api/v1/leads/{lead_id}")
def api_lead(lead_id: str, db: TenantSession = Depends(get_db), p: Principal = Depends(member_api("read"))):
    return _lead_json(_get(db, Lead, lead_id))


@router.patch("/api/v1/leads/{lead_id}")
async def api_lead_update(lead_id: str, request: Request, db: TenantSession = Depends(get_db),
                          p: Principal = Depends(member_api("leads.write"))):
    lead = _get(db, Lead, lead_id)
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(422, "Invalid JSON") from None
    if not isinstance(body, dict) or set(body) - {"status", "follow_up_on"}:
        raise HTTPException(422, "Only status and follow_up_on can be changed.")
    if "status" in body:
        if body["status"] not in LEAD_STATUSES:
            raise HTTPException(422, "Unknown status")
        lead.status = body["status"]
    if "follow_up_on" in body:
        try:
            lead.follow_up_on = date.fromisoformat(body["follow_up_on"]) if body["follow_up_on"] else None
        except (TypeError, ValueError):
            raise HTTPException(422, "Invalid date") from None
    audit(db, p.org.id, p.user.id, "lead.updated", "lead", lead.id, {"via": "api"})
    return _lead_json(lead)


@router.get("/api/v1/documents")
def api_documents(db: TenantSession = Depends(get_db), p: Principal = Depends(member_api("read"))):
    return {"documents": [{"id": str(d.id), "filename": d.filename, "size": d.size, "sha256": d.sha256}
                          for d in db.scalars(select(Document).order_by(Document.created_at.desc()).limit(300))]}


@router.get("/api/v1/site")
def api_site(db: TenantSession = Depends(get_db), p: Principal = Depends(member_api("read"))):
    site = sites.get_site(db)
    if site is None:
        return {"site": None}
    return {"site": {"id": str(site.id), "slug": site.slug, "published": site.published_version_id is not None,
                     "hosts": [h.hostname for h in sites.list_hosts(db, site)], "draft": site.draft}}


@router.get("/api/v1/improvements")
def api_improvements(db: TenantSession = Depends(get_db), p: Principal = Depends(member_api("read"))):
    return {"improvements": [{"id": str(i.id), "title": i.title, "status": i.status,
                              "evidence_status": i.evidence_status, "metric": i.metric}
                             for i in db.scalars(select(Improvement).order_by(Improvement.created_at))]}


__all__ = ["router", "timedelta", "jobs"]
