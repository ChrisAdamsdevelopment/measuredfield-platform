"""MeasuredField operator console (/operator).

Operators run the business: they see prospects, orders, the email outbox, the job queue
and the shared-lesson review queue. They do NOT get a back door into customer workspaces:
there is no operator route that reads tenant tables, and the database role has no
row-level-security bypass. The only tenant-side effects from here are provisioning a new
organization, verifying a custom domain, and marking a candidate lesson decided.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, back, operator, operator_post, parse_uuid, render
from ..models import (Assessment, EmailMessage, Job, LessonReview, ServiceOrder, SharedSkill,
                      SharedSkillVersion, SiteHost, now)
from ..services import jobs, learning, orders
from ..services.audit import audit

router = APIRouter(prefix="/operator")


@router.get("")
def overview(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(operator)):
    settings = get_settings()
    order_rows = db.execute(select(ServiceOrder, Assessment).join(Assessment, Assessment.id == ServiceOrder.assessment_id)
                            .order_by(ServiceOrder.created_at.desc()).limit(50)).all()
    return render(request, "operator/overview.html", {
        "assessments": db.scalars(select(Assessment).order_by(Assessment.created_at.desc()).limit(50)).all(),
        "orders": order_rows,
        "pending_reviews": db.scalar(select(func.count()).select_from(LessonReview).where(LessonReview.status == "pending")),
        "outbox": dict(db.execute(select(EmailMessage.status, func.count()).group_by(EmailMessage.status)).all()),
        "jobs": dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all()),
        "pending_domains": db.scalars(select(SiteHost).where(SiteHost.kind == "custom", SiteHost.verified_at.is_(None))).all(),
        "email_live": settings.email_provider != "none", "payments_live": settings.payment_provider != "none"})


@router.get("/assessments/{assessment_id}")
def assessment(assessment_id: str, request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(operator)):
    row = db.get(Assessment, parse_uuid(assessment_id))
    if row is None:
        raise HTTPException(404, "Not found")
    return render(request, "operator/assessment.html", {
        "a": row, "r": row.result,
        "orders": db.scalars(select(ServiceOrder).where(ServiceOrder.assessment_id == row.id)).all()})


def _order(db: TenantSession, order_id: str) -> ServiceOrder:
    order = db.scalar(select(ServiceOrder).where(ServiceOrder.id == parse_uuid(order_id)).with_for_update())
    if order is None:
        raise HTTPException(404, "Not found")
    return order


@router.post("/orders/{order_id}/mark-paid")
def order_mark_paid(order_id: str, reference: str = Form(""), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(operator_post)):
    """Record an evidenced payment received outside the platform."""
    reference = reference.strip()
    if not 4 <= len(reference) <= 120:
        return back("/operator", err="Enter a payment reference of 4 to 120 characters.")
    order = _order(db, order_id)
    try:
        orders.mark_paid(db, order, provider="manual", evidence=f"manual by {p.user.email}: {reference}")
    except orders.OrderError as exc:
        return back("/operator", err=str(exc))
    return back("/operator", ok="Payment recorded. The workspace will be created by the job queue.")


@router.post("/orders/{order_id}/provision")
def order_provision(order_id: str, reason: str = Form(""), db: TenantSession = Depends(get_db),
                    p: Principal = Depends(operator_post)):
    order = _order(db, order_id)
    try:
        orders.provision(db, order, operator_id=p.user.id, unpaid_reason=reason)
    except orders.OrderError as exc:
        return back("/operator", err=str(exc))
    db.set_context(None, p.user.id)
    return back("/operator", ok="Workspace created and owner invited.")


@router.get("/outbox")
def outbox(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(operator)):
    return render(request, "operator/outbox.html", {
        "messages": db.scalars(select(EmailMessage).order_by(EmailMessage.created_at.desc()).limit(100)).all(),
        "email_live": get_settings().email_provider != "none"})


@router.post("/jobs/run")
def jobs_run(p: Principal = Depends(operator_post)):
    ran = jobs.run_due(50)
    return back("/operator", ok=f"Ran {ran} queued job(s)")


@router.post("/domains/verify")
def domain_verify(hostname: str = Form(""), db: TenantSession = Depends(get_db), p: Principal = Depends(operator_post)):
    """Confirm a custom domain after its DNS and TLS are set up on the hosting platform."""
    host = db.get(SiteHost, hostname.strip().lower())
    if host is None or host.kind != "custom":
        raise HTTPException(404, "Not found")
    host.verified_at = now()
    db.set_context(host.org_id, None)
    audit(db, host.org_id, p.user.id, "site.domain_verified", "site", host.site_id, {"hostname": host.hostname})
    db.flush()
    db.set_context(None, p.user.id)
    return back("/operator", ok="Domain verified")


@router.get("/reviews")
def reviews(request: Request, db: TenantSession = Depends(get_db), p: Principal = Depends(operator)):
    skills = db.execute(
        select(SharedSkill, SharedSkillVersion).outerjoin(SharedSkillVersion, SharedSkillVersion.id == SharedSkill.active_version_id)
        .order_by(SharedSkill.title)).all()
    return render(request, "operator/reviews.html", {
        "reviews": db.scalars(select(LessonReview).order_by(LessonReview.created_at.desc()).limit(100)).all(),
        "skills": skills})


@router.post("/reviews/{review_id}/decide")
def review_decide(review_id: str, decision: str = Form(""), skill_slug: str = Form(""),
                  db: TenantSession = Depends(get_db), p: Principal = Depends(operator_post)):
    review = db.get(LessonReview, parse_uuid(review_id))
    if review is None:
        raise HTTPException(404, "Not found")
    if decision not in ("approve", "reject"):
        raise HTTPException(422, "Choose approve or reject")
    try:
        version = learning.decide_review(db, review, p.user, approve=decision == "approve", skill_slug=skill_slug)
    except learning.LearningError as exc:
        return back("/operator/reviews", err=str(exc))
    db.set_context(None, p.user.id)
    if decision == "approve" and version is None:
        return back("/operator/reviews", err="The source business withdrew consent; nothing was published.")
    return back("/operator/reviews", ok="Published as a new version" if version else "Rejected")


@router.post("/skills/{skill_id}/rollback")
def skill_rollback(skill_id: str, db: TenantSession = Depends(get_db), p: Principal = Depends(operator_post)):
    skill = db.get(SharedSkill, parse_uuid(skill_id))
    if skill is None:
        raise HTTPException(404, "Not found")
    learning.rollback_skill(db, skill, p.user)
    return back("/operator/reviews", ok="Active version revoked")
