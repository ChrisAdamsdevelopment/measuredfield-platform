"""Sign-in, sign-out, invitations, password recovery and workspace switching."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, back, client_key, get_principal, parse_uuid, render, safe_local_redirect, user_post
from ..models import Membership, Organization, User, now
from ..security import (SESSION_COOKIE, create_session, hash_password, issue_token, normalize_email,
                        password_problem, peek_token, rate_limited, revoke_user_sessions, valid_email,
                        verify_password)
from ..services import jobs, orders
from ..services.email import queue_email

router = APIRouter()
GENERIC_LOGIN_ERROR = "That email and password don't match an account."


def _safe_next(value: str) -> str:
    # The browser strips ASCII control characters before interpreting authority.
    # Keep strict local navigation; never forward a crafted cross-origin URL.
    return value if len(value) < 200 and safe_local_redirect(value) else "/app"


def _set_cookie(response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.is_production, samesite="lax",
                        max_age=settings.session_ttl_hours * 3600, path="/")


def _first_org(db: TenantSession, user: User):
    db.set_context(None, user.id)
    return db.scalar(select(Membership.org_id).where(Membership.user_id == user.id).order_by(Membership.created_at))


@router.get("/login")
def login_form(request: Request, next: str = "/app", principal: Principal | None = Depends(get_principal)):
    if principal is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "public/login.html", {"next": _safe_next(next), "error": "", "email": ""})


@router.post("/login")
def login(request: Request, db: TenantSession = Depends(get_db), email: str = Form(""),
          password: str = Form(""), next: str = Form("/app")):
    email = normalize_email(email)
    # Limits per client and per account, so neither a single address nor a spread of
    # addresses can grind one mailbox.
    limited = rate_limited(db, "login-ip:" + client_key(request), limit=20, window_seconds=900)
    limited = rate_limited(db, "login-acct:" + email[:80], limit=8, window_seconds=900) or limited
    db.commit()
    if limited:
        return render(request, "public/login.html",
                      {"next": _safe_next(next), "email": email,
                       "error": "Too many sign-in attempts. Wait 15 minutes and try again."}, status_code=429)
    user = db.scalar(select(User).where(User.email == email)) if valid_email(email) else None
    ok = verify_password(user.password_hash if user else None, password[:200])
    if not ok or user is None or user.disabled_at is not None:
        return render(request, "public/login.html",
                      {"next": _safe_next(next), "email": email, "error": GENERIC_LOGIN_ERROR}, status_code=401)
    token = create_session(db, user, _first_org(db, user))
    target = _safe_next(next)
    if user.is_platform_admin and target == "/app" and _first_org(db, user) is None:
        target = "/operator"
    response = RedirectResponse(target, status_code=303)
    _set_cookie(response, token)
    return response


@router.post("/logout")
def logout(principal: Principal = Depends(user_post)):
    principal.session.revoked_at = now()
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/app/switch-org")
def switch_org(org_id: str = Form(""), db: TenantSession = Depends(get_db), principal: Principal = Depends(user_post)):
    target = parse_uuid(org_id)
    db.set_context(None, principal.user.id)
    membership = db.scalar(select(Membership).where(Membership.user_id == principal.user.id, Membership.org_id == target))
    if membership is not None:
        principal.session.active_org_id = target
    return RedirectResponse("/app", status_code=303)


# ----------------------------------------------------------------------- invitations

@router.get("/accept-invite")
def invite_form(request: Request, token: str = "", db: TenantSession = Depends(get_db)):
    row = peek_token(db, token, "invite")
    if row is None:
        return render(request, "public/message.html", {
            "title": "This invitation link has expired or was already used",
            "body": "Ask the person who invited you to send a new one."}, status_code=410)
    existing = db.scalar(select(User).where(User.email == row.email))
    return render(request, "public/accept_invite.html", {
        "token": token, "email": row.email, "has_account": bool(existing and existing.password_hash), "error": ""})


@router.post("/accept-invite")
def invite_accept(request: Request, db: TenantSession = Depends(get_db), token: str = Form(""),
                  name: str = Form(""), password: str = Form("")):
    if rate_limited(db, "invite:" + client_key(request), limit=15, window_seconds=900):
        db.commit()
        return render(request, "public/message.html", {"title": "Too many attempts", "body": "Try again in 15 minutes."}, status_code=429)
    row = peek_token(db, token, "invite")
    if row is None:
        return render(request, "public/message.html", {
            "title": "This invitation link has expired or was already used",
            "body": "Ask the person who invited you to send a new one."}, status_code=410)
    user = db.scalar(select(User).where(User.email == row.email))
    has_account = bool(user and user.password_hash)

    def again(error: str):
        return render(request, "public/accept_invite.html",
                      {"token": token, "email": row.email, "has_account": has_account, "error": error}, status_code=422)

    if has_account:
        if not verify_password(user.password_hash, password[:200]) or user.disabled_at is not None:
            return again("That password doesn't match your existing account.")
    else:
        problem = password_problem(password)
        if problem:
            return again(problem)
        name = " ".join(name.split())[:120]
        if len(name) < 2:
            return again("Enter your name.")
        if user is None:
            user = User(email=row.email, name=name)
            db.add(user)
        user.name, user.password_hash = name, hash_password(password)
        db.flush()
    orders.accept_invite(db, row, user)
    session_token = create_session(db, user, row.org_id)
    response = back("/app", ok="Welcome to your workspace")
    _set_cookie(response, session_token)
    return response


# ------------------------------------------------------------------ password recovery

@router.get("/forgot")
def forgot_form(request: Request):
    return render(request, "public/forgot.html", {"sent": False})


@router.post("/forgot")
def forgot(request: Request, db: TenantSession = Depends(get_db), email: str = Form("")):
    settings = get_settings()
    email = normalize_email(email)
    limited = rate_limited(db, "forgot:" + client_key(request), limit=5, window_seconds=3600)
    db.commit()
    if not limited and valid_email(email):
        user = db.scalar(select(User).where(User.email == email, User.disabled_at.is_(None)))
        if user is not None:
            token = issue_token(db, purpose="reset", email=email, ttl=timedelta(minutes=settings.reset_ttl_minutes))
            message = queue_email(
                db, kind="password_reset", to=email, subject=f"Reset your {settings.brand_name} password",
                body=(f"Use this link to choose a new password (expires in {settings.reset_ttl_minutes} minutes):\n"
                      f"{settings.public_base_url}/reset?token={token}\n\n"
                      "If you didn't ask for this, you can ignore this message."))
            jobs.enqueue(db, "send_email", {"message_id": str(message.id)}, key=f"email:{message.id}")
    # Same answer whether or not the account exists.
    return render(request, "public/forgot.html", {"sent": True})


@router.get("/reset")
def reset_form(request: Request, token: str = "", db: TenantSession = Depends(get_db)):
    if peek_token(db, token, "reset") is None:
        return render(request, "public/message.html", {
            "title": "This reset link has expired or was already used",
            "body": "Request a new one from the sign-in page."}, status_code=410)
    return render(request, "public/reset.html", {"token": token, "error": ""})


@router.post("/reset")
def reset(request: Request, db: TenantSession = Depends(get_db), token: str = Form(""), password: str = Form("")):
    if rate_limited(db, "reset:" + client_key(request), limit=10, window_seconds=900):
        db.commit()
        return render(request, "public/message.html", {"title": "Too many attempts", "body": "Try again in 15 minutes."}, status_code=429)
    row = peek_token(db, token, "reset")
    if row is None:
        return render(request, "public/message.html", {
            "title": "This reset link has expired or was already used",
            "body": "Request a new one from the sign-in page."}, status_code=410)
    problem = password_problem(password)
    if problem:
        return render(request, "public/reset.html", {"token": token, "error": problem}, status_code=422)
    user = db.scalar(select(User).where(User.email == row.email))
    if user is None:
        return RedirectResponse("/login", status_code=303)
    user.password_hash = hash_password(password)
    row.used_at = now()
    revoke_user_sessions(db, user.id)  # a reset signs out every existing session
    return back("/login", ok="Password updated. Please sign in.")


def list_orgs(db: TenantSession, user: User) -> list[dict]:
    """Workspaces the signed-in user belongs to.

    Row-level security shows a user's own memberships across organizations only while NO
    organization is active, so this briefly steps out of the tenant context and restores it.
    Only id and name are read."""
    active = db.org_id
    db.set_context(None, user.id)
    try:
        rows = db.execute(
            select(Organization.id, Organization.name)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == user.id).order_by(Organization.name)).all()
    finally:
        db.set_context(active, user.id)
    return [{"id": r.id, "name": r.name} for r in rows]
