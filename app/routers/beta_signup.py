"""Email-verified account setup for verified direct Stripe purchasers.

Signup never provisions an anonymous paid entitlement or bypasses Stripe. An
existing pending invite may be resent under strict rate limits. The email
recipient must still redeem the time-limited invitation before login.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..db import TenantSession, get_db
from ..deps import client_key, render
from ..models import AuthToken, EmailMessage, Organization, PendingPaymentLinkClaim, now
from ..security import digest, normalize_email, rate_limited, valid_email
from ..services import jobs, sitegen
from ..services.audit import audit
from ..services.email import AUTH_LINK_PLACEHOLDER, get_provider, queue_auth_email

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
    pending = db.scalar(select(AuthToken).where(
        AuthToken.email == values["email"], AuthToken.purpose == "invite",
        AuthToken.role == "owner", AuthToken.used_at.is_(None),
        AuthToken.expires_at > now(),
    ).order_by(AuthToken.created_at.desc()).limit(1).with_for_update())
    if pending is not None:
        # A previous accepted/undelivered email must not make retrying signup a
        # permanent no-op. Reuse the SAME owner invitation and organization;
        # do not grant membership, create a second tenant, or mint live tokens.
        last = db.scalar(select(EmailMessage).where(
            EmailMessage.auth_token_id == pending.id,
            EmailMessage.kind == "invite",
        ).order_by(EmailMessage.created_at.desc()).limit(1))
        if last is None or last.created_at <= now() - timedelta(minutes=3):
            from ..config import get_settings
            settings = get_settings()
            followup = EmailMessage(
                kind="invite", to_email=values["email"],
                org_id=pending.org_id,
                subject="Your MeasuredField account verification link",
                body=("You requested another one-time verification link. "
                      "Open this link to finish setting up your MeasuredField account:\n"
                      + settings.public_base_url.rstrip("/")
                      + "/accept-invite?token=" + AUTH_LINK_PLACEHOLDER
                      + "\n\nIf you didn't request it, ignore this message.\n"),
                auth_token_id=pending.id,
            )
            db.add(followup)
            db.flush()
            jobs.enqueue(db, "send_email", {"message_id": str(followup.id)},
                         key=f"email:{followup.id}")
        return RedirectResponse("/signup/thanks", status_code=303)

    # Only a verified signed Stripe Payment Link Checkout can open a NEW
    # business-account invitation. An anonymous signup is not a free plan.
    claim = db.scalar(select(PendingPaymentLinkClaim.id).where(
        PendingPaymentLinkClaim.billing_email == values["email"],
        PendingPaymentLinkClaim.offer_code == "operate",
        PendingPaymentLinkClaim.status == "pending_claim",
    ).order_by(PendingPaymentLinkClaim.verified_at.desc()).limit(1))
    if claim is None:
        return _form(request, values=values, errors={
            "form": ("Complete Business Workspace checkout first, then register "
                     "with the same email address. Already registered? "
                     "Use Sign in or Reset password.")
        }, status=422)

    org = Organization(
        id=uuid.uuid4(), name=values["business_name"], industry=values["industry"],
        plan="purchase_pending",
        settings={
            "billing": {"offer": "Business Workspace checkout pending claim",
                        "paid": False, "amount_cents": 0},
            "onboarding": {"source": "verified_stripe_payment_link",
                           "pending_claim_confirmation": True},
        },
    )
    db.set_context(org.id, None)
    db.add(org)
    db.flush()
    audit(db, org.id, None, "purchase.signup_requested", "organization", org.id,
          {"email_verification_required": True, "paid": False})
    db.set_context(None, None)

    msg = queue_auth_email(
        db, kind="invite", to=values["email"], org_id=org.id, role="owner",
        subject="Verify your MeasuredField Business Workspace account",
        intro=("You requested an account for " + org.name
               + ". Open this one-time link to verify your email and set your password:"),
        outro=("After signing in, open Billing > Connect a verified purchase to "
               "activate your Stripe subscription. If you didn't request "
               "this invitation, ignore this email."),
    )
    jobs.enqueue(db, "send_email", {"message_id": str(msg.id)}, key=f"email:{msg.id}")
    return RedirectResponse("/signup/thanks", status_code=303)
