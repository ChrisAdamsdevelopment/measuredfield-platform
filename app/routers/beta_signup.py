"""Email-verified, no-card self-service workspace enrollment for the founding beta.

An anonymous browser may request an invitation, but never receives a usable token,
account or tenant membership. Only /accept-invite can activate membership after
the invited email recipient proves control of the address.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..db import TenantSession, get_db
from ..deps import client_key, render
from ..models import AuthToken, Organization, now
from ..security import digest, normalize_email, rate_limited, valid_email
from ..services import jobs, sitegen
from ..services.audit import audit
from ..services.email import get_provider, queue_auth_email

router = APIRouter()
ALLOWED_INDUSTRIES = frozenset(sitegen.INDUSTRIES)


def _clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _form(request: Request, *, values: dict, errors: dict, status: int = 200):
    return render(request, "public/beta_signup.html", {
        "values": values,
        "errors": errors,
        "available": get_provider() is not None,
    }, status_code=status)


@router.get("/signup")
def signup(request: Request):
    return _form(request, values={}, errors={})


@router.get("/signup/thanks")
def thanks(request: Request):
    return render(request, "public/beta_signup_thanks.html")


@router.post("/signup")
def request_invite(
    request: Request, db: TenantSession = Depends(get_db),
    business_name: str = Form(""), contact_name: str = Form(""),
    email: str = Form(""), industry: str = Form("other"),
    consent: str = Form(""), company_site: str = Form(""),
):
    if get_provider() is None:
        return _form(request, values={}, errors={"form": "Invitations are temporarily unavailable. Please try again later."}, status=503)
    # Bot trap must not reveal anything about an email or cause a database write.
    if company_site:
        return RedirectResponse("/signup/thanks", status_code=303)
    values = {
        "business_name": _clean(business_name, 120),
        "contact_name": _clean(contact_name, 120),
        "email": normalize_email(email),
        "industry": industry if industry in ALLOWED_INDUSTRIES else "other",
    }
    errors = {}
    if not 2 <= len(values["business_name"]) <= 120:
        errors["business_name"] = "Enter a business name."
    if not 2 <= len(values["contact_name"]) <= 120:
        errors["contact_name"] = "Enter your name."
    if not valid_email(values["email"]):
        errors["email"] = "Enter a valid email address."
    if industry not in ALLOWED_INDUSTRIES:
        errors["industry"] = "Choose a business type."
    if consent != "yes":
        errors["consent"] = "Please agree to the terms and privacy information."
    if errors:
        return _form(request, values=values, errors=errors, status=422)

    # Both limits are database-atomic; the email is hashed before persistence.
    limited_by_ip = rate_limited(db, "beta-signup-ip:" + client_key(request), limit=20, window_seconds=3600)
    limited_by_email = rate_limited(db, "beta-signup-mail:" + digest(values["email"]), limit=3, window_seconds=86400)
    if limited_by_ip or limited_by_email:
        db.commit()
        return _form(request, values=values,
                     errors={"form": "Too many invitations requested. Please try again later."}, status=429)

    # Suppress duplicate unaccepted invitations, including those from existing operator
    # workflows. The response is generic, preventing account/tenant enumeration.
    pending = db.scalar(select(AuthToken.id).where(
        AuthToken.email == values["email"], AuthToken.purpose == "invite",
        AuthToken.role == "owner", AuthToken.used_at.is_(None),
        AuthToken.expires_at > now(),
    ).limit(1))
    if pending is not None:
        return RedirectResponse("/signup/thanks", status_code=303)

    org = Organization(
        id=uuid.uuid4(), name=values["business_name"], industry=values["industry"],
        plan="founding_beta",
        settings={
            "billing": {"offer": "Founding beta pilot", "paid": False, "amount_cents": 0},
            "beta": {"source": "verified_email_self_signup", "card_required": False},
        },
    )
    db.set_context(org.id, None)
    db.add(org)
    db.flush()
    audit(db, org.id, None, "beta.signup_requested", "organization", org.id,
          {"email_verification_required": True, "paid": False})
    db.set_context(None, None)

    msg = queue_auth_email(
        db, kind="invite", to=values["email"], org_id=org.id, role="owner",
        subject="Confirm your free MeasuredField workspace",
        intro=("You requested a free founding-beta workspace for " + org.name
               + ". Open this one-time link to verify your email and finish creating your workspace:"),
        outro=("No card is needed for founding-beta access. If you didn't request"
               " this invitation, ignore this email."),
    )
    jobs.enqueue(db, "send_email", {"message_id": str(msg.id)}, key=f"email:{msg.id}")
    return RedirectResponse("/signup/thanks", status_code=303)
