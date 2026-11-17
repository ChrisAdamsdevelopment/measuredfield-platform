"""MF-080: intentional, authenticated beta feedback and operator triage.

This is a PLATFORM support inbox, not a backdoor into tenant records.
Submissions contain only text explicitly typed by a signed-in tester.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select

from ..db import TenantSession, get_db
from ..deps import (
    Principal, back, member, member_post, operator, operator_post, parse_uuid, render,
)
from ..models import BetaFeedback, User, now
from ..security import rate_limited
from .auth import list_orgs

router = APIRouter()
KINDS = {"bug": "Bug report", "idea": "Feature idea", "other": "General feedback"}
STATES = {"new", "reviewing", "resolved"}
MAX_HOURLY_SUBMISSIONS = 8


def _feedback_page(request: Request, db: TenantSession, p: Principal, *,
                   error: str = "", message: str = "", kind: str = "bug",
                   status_code: int = 200):
    rows = db.scalars(
        select(BetaFeedback)
        .where(BetaFeedback.submitter_user_id == p.user.id,
               BetaFeedback.workspace_name == p.org.name)
        .order_by(BetaFeedback.created_at.desc()).limit(30)
    ).all()
    response = render(request, "app/feedback.html", {
        "org": p.org, "role": p.role, "can": p.can, "orgs": list_orgs(db, p.user),
        "recent_feedback": rows, "kinds": KINDS,
        "form_error": error, "draft_message": message, "draft_kind": kind,
    }, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/app/feedback")
def feedback_form(request: Request, db: TenantSession = Depends(get_db),
                  p: Principal = Depends(member())):
    return _feedback_page(request, db, p)


@router.post("/app/feedback")
def submit_feedback(
    request: Request, kind: str = Form("bug"), message: str = Form(""),
    acknowledge: str = Form(""), db: TenantSession = Depends(get_db),
    p: Principal = Depends(member_post("read")),
):
    # No cookies bypass: the standard member_post dependency checks CSRF.
    # Count attempts even if invalid; the quota uses the authenticated user.
    limited = rate_limited(db, f"beta_feedback:{p.user.id}", limit=MAX_HOURLY_SUBMISSIONS,
                           window_seconds=3600)
    db.commit()
    if limited:
        return _feedback_page(request, db, p, error="Submission limit reached. Try again later.",
                              status_code=429)
    text = message.strip()
    if kind not in KINDS:
        return _feedback_page(request, db, p, error="Select a feedback category.",
                              message=text[:1800], status_code=422)
    if len(text) < 15 or len(text) > 1800:
        return _feedback_page(request, db, p,
                              error="Please describe your feedback in 15–1,800 characters.",
                              message=text[:1800], kind=kind, status_code=422)
    if acknowledge != "yes":
        return _feedback_page(request, db, p,
                              error="Please confirm this is sent to MeasuredField support.",
                              message=text, kind=kind, status_code=422)
    db.add(BetaFeedback(
        submitter_user_id=p.user.id, workspace_name=p.org.name[:120],
        kind=kind, message=text, status="new",
    ))
    # The request-session middleware commits before redirect is released.
    return back("/app/feedback", ok="Feedback received. Thank you for helping improve MeasuredField.")


@router.get("/operator/feedback")
def operator_feedback(request: Request, db: TenantSession = Depends(get_db),
                      p: Principal = Depends(operator)):
    rows = db.execute(
        select(BetaFeedback, User.email)
        .join(User, User.id == BetaFeedback.submitter_user_id)
        .order_by(BetaFeedback.created_at.desc()).limit(150)
    ).all()
    response = render(request, "operator/feedback.html", {
        "items": rows, "statuses": ("new", "reviewing", "resolved"),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/operator/feedback/{feedback_id}/status")
def update_feedback_status(
    feedback_id: str, status: str = Form(""),
    db: TenantSession = Depends(get_db), p: Principal = Depends(operator_post),
):
    if status not in STATES:
        raise HTTPException(422, "Unsupported feedback status")
    row = db.get(BetaFeedback, parse_uuid(feedback_id))
    if row is None:
        raise HTTPException(404, "Feedback not found")
    row.status = status
    row.reviewed_by_user_id = p.user.id
    row.updated_at = now()
    return back("/operator/feedback", ok="Feedback status updated.")
