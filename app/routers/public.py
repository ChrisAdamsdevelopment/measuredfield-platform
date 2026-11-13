"""MeasuredField's own public website: marketing, the two-question assessment,
service selection and approval, payment webhook, and customer-facing proposal links."""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select

from ..config import get_settings
from ..db import TenantSession, get_db
from ..deps import Principal, client_key, get_principal, parse_uuid, render
from ..models import Assessment, Customer, Organization, Proposal, ServiceOrder, now
from ..security import digest, new_token, normalize_email, rate_limited, valid_email
from ..services import orders, payments, sitegen
from ..services.assessment import AssessmentInput, assess
from ..services.audit import audit

router = APIRouter()


@router.get("/")
def home(request: Request, principal: Principal | None = Depends(get_principal)):
    return render(request, "public/home.html", {"offers": get_settings().offers})


@router.get("/robots.txt")
def public_robots():
    settings = get_settings()
    if settings.env != "production":
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse(
        "User-agent: *\nAllow: /\nSitemap: "
        + settings.public_base_url.rstrip("/") + "/sitemap.xml\n"
    )


@router.get("/sitemap.xml")
def public_sitemap():
    """Canonical MeasuredField app sitemap; no private/customer URL disclosure."""
    from xml.sax.saxutils import escape as xml_escape

    settings = get_settings()
    if settings.env != "production":
        raise HTTPException(404, "Not found")
    base = settings.public_base_url.rstrip("/")
    paths = ["/", "/assessment", "/watch/measuredfield-business-launch", "/privacy", "/terms"]
    entries = []
    for path in paths:
        extra = ""
        if path.endswith("/measuredfield-business-launch") and settings.video_publication_date:
            video = (
                "<video:video>"
                "<video:thumbnail_loc>" + xml_escape(base + "/static/media/measuredfield-business-launch-film-poster.jpg") + "</video:thumbnail_loc>"
                "<video:title>MeasuredField business launch commercial</video:title>"
                "<video:description>A 30-second introduction to clearer business websites and inquiry management.</video:description>"
                "<video:content_loc>" + xml_escape(base + "/static/media/measuredfield-business-launch-film-portrait.mp4") + "</video:content_loc>"
                "<video:publication_date>" + xml_escape(settings.video_publication_date) + "</video:publication_date>"
                "</video:video>"
            )
            extra = video
        entries.append("<url><loc>" + xml_escape(base + path) + "</loc>" + extra + "</url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">'
        + "".join(entries) + "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/watch/measuredfield-business-launch")
def watch_measuredfield_launch(request: Request, principal: Principal | None = Depends(get_principal)):
    return render(request, "public/watch.html")


@router.get("/privacy")
def privacy(request: Request, principal: Principal | None = Depends(get_principal)):
    return render(request, "public/privacy.html")


@router.get("/terms")
def terms(request: Request, principal: Principal | None = Depends(get_principal)):
    return render(request, "public/terms.html")


# ------------------------------------------------------------------------ assessment

def _clean_line(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


@router.get("/assessment")
def assessment_form(request: Request, principal: Principal | None = Depends(get_principal)):
    return render(request, "public/assessment.html", {"values": {}, "errors": {}, "industries": sitegen.INDUSTRIES})


@router.post("/assessment")
def assessment_submit(
    request: Request, db: TenantSession = Depends(get_db),
    excited: str = Form(""), blocker: str = Form(""), business_name: str = Form(""),
    contact_name: str = Form(""), email: str = Form(""), website: str = Form(""),
    industry: str = Form("other"), consent: str = Form(""), company_site: str = Form(""),
):
    values = {"excited": excited.strip()[:1500], "blocker": blocker.strip()[:1500],
              "business_name": _clean_line(business_name, 120), "contact_name": _clean_line(contact_name, 120),
              "email": normalize_email(email), "website": website.strip()[:250],
              "industry": industry if industry in sitegen.INDUSTRIES else "other"}
    errors: dict[str, str] = {}
    if len(values["excited"]) < 15:
        errors["excited"] = "Tell us a little more: a sentence or two is plenty."
    if len(values["blocker"]) < 15:
        errors["blocker"] = "Tell us a little more: a sentence or two is plenty."
    if len(values["business_name"]) < 2:
        errors["business_name"] = "Enter your business name (or the name you're planning to use)."
    if len(values["contact_name"]) < 2:
        errors["contact_name"] = "Enter your name."
    if not valid_email(values["email"]):
        errors["email"] = "Enter a valid email address."
    if values["website"]:
        parts = urlsplit(values["website"] if "://" in values["website"] else "https://" + values["website"])
        if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname \
                or parts.username or parts.password:
            errors["website"] = "Enter a website address like yourbusiness.com, or leave this empty."
    if consent != "yes":
        errors["consent"] = "Please confirm we may contact you about this request."
    if errors:
        return render(request, "public/assessment.html",
                      {"values": values, "errors": errors, "industries": sitegen.INDUSTRIES}, status_code=422)
    if company_site:  # honeypot: pretend success, store nothing
        return RedirectResponse("/assessment/thanks", status_code=303)
    if rate_limited(db, "assess:" + client_key(request), limit=6, window_seconds=3600):
        db.commit()
        return render(request, "public/assessment.html",
                      {"values": values, "errors": {"form": "You've sent several requests. Please try again in an hour."},
                       "industries": sitegen.INDUSTRIES}, status_code=429)
    result = assess(AssessmentInput(business_name=values["business_name"], excited=values["excited"],
                                    blocker=values["blocker"], website=values["website"], industry=values["industry"]))
    token = new_token()
    # The website address is stored as text only. It is never fetched from this server.
    db.add(Assessment(token_hash=digest(token), business_name=values["business_name"],
                      contact_name=values["contact_name"], email=values["email"], website=values["website"],
                      industry=values["industry"], excited_text=values["excited"], blocker_text=values["blocker"],
                      result=result, consent_at=now()))
    return RedirectResponse(f"/assessment/r/{token}", status_code=303)


@router.get("/assessment/thanks")
def assessment_thanks(request: Request):
    return render(request, "public/message.html", {"title": "Thank you", "body": "We received your request."})


def _assessment(db: TenantSession, token: str) -> Assessment:
    row = db.scalar(select(Assessment).where(Assessment.token_hash == digest(token))) if 20 <= len(token) <= 100 else None
    if row is None:
        raise HTTPException(404, "We couldn't find that assessment. The link may be incomplete.")
    return row


@router.get("/assessment/r/{token}")
def assessment_result(token: str, request: Request, db: TenantSession = Depends(get_db),
                      principal: Principal | None = Depends(get_principal)):
    row = _assessment(db, token)
    settings = get_settings()
    order = db.scalar(select(ServiceOrder).where(ServiceOrder.assessment_id == row.id,
                                                 ServiceOrder.status != "cancelled").order_by(ServiceOrder.created_at.desc()))
    return render(request, "public/result.html", {
        "a": row, "r": row.result, "token": token, "offers": settings.offers, "order": order,
        "payments_live": payments.get_provider() is not None, "errors": {}})


@router.post("/assessment/r/{token}/order")
def order_create(token: str, request: Request, db: TenantSession = Depends(get_db),
                 offer_code: str = Form(""), approved_name: str = Form(""), agree: str = Form("")):
    row = _assessment(db, token)
    settings = get_settings()
    existing = db.scalar(select(ServiceOrder).where(ServiceOrder.assessment_id == row.id, ServiceOrder.status != "cancelled"))
    if existing is not None:
        return RedirectResponse(f"/order/{existing.id}?t={token}", status_code=303)
    if rate_limited(db, "order:" + client_key(request), limit=10, window_seconds=3600):
        db.commit()
        raise HTTPException(429, "Too many attempts. Please try again later.")
    errors = {}
    if agree != "yes":
        errors["agree"] = "Please confirm you've read the terms."
    try:
        if not errors:
            order = orders.create_order(db, row, offer_code, approved_name)
    except orders.OrderError as exc:
        errors["form"] = str(exc)
    if errors:
        return render(request, "public/result.html", {
            "a": row, "r": row.result, "token": token, "offers": settings.offers, "order": None,
            "payments_live": payments.get_provider() is not None, "errors": errors,
            "chosen": offer_code}, status_code=422)
    provider = payments.get_provider()
    if order.amount_cents > 0 and provider is not None:
        try:
            checkout = provider.create_checkout(
                order_id=str(order.id), offer=order.offer_snapshot, customer_email=row.email,
                success_url=f"{settings.public_base_url}/order/{order.id}?t={token}",
                cancel_url=f"{settings.public_base_url}/order/{order.id}?t={token}")
        except payments.PaymentError:
            # Approval stays recorded; the status page explains that payment is still open.
            return RedirectResponse(f"/order/{order.id}?t={token}", status_code=303)
        order.provider_ref, order.payment_provider, order.status = checkout["id"], "stripe", "payment_pending"
        return RedirectResponse(checkout["url"], status_code=303)
    return RedirectResponse(f"/order/{order.id}?t={token}", status_code=303)


@router.get("/order/{order_id}")
def order_status(order_id: str, t: str, request: Request, db: TenantSession = Depends(get_db)):
    row = _assessment(db, t)
    order = db.get(ServiceOrder, parse_uuid(order_id))
    if order is None or order.assessment_id != row.id:
        raise HTTPException(404, "Not found")
    return render(request, "public/order.html", {"a": row, "order": order, "token": t,
                                                 "payments_live": payments.get_provider() is not None})


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: TenantSession = Depends(get_db)):
    provider = payments.get_provider()
    if provider is None:
        return JSONResponse({"error": "Payments are not enabled."}, status_code=404)
    payload = await request.body()
    if len(payload) > 512_000:
        return JSONResponse({"error": "Payload too large."}, status_code=413)
    try:
        event = provider.verify_webhook(payload, request.headers.get("stripe-signature", ""))
    except payments.PaymentError:
        return JSONResponse({"error": "Invalid signature."}, status_code=400)
    outcome = orders.handle_stripe_event(db, event)
    return JSONResponse({"received": True, "outcome": outcome})


# ------------------------------------------------- proposals sent by customer businesses

def _proposal(db: TenantSession, token: str) -> tuple[Proposal, Organization, Customer]:
    """Token = <org id hex>.<secret>. The org part only selects which tenant to look in;
    the row is found by the hash of the whole token, so a forged org part finds nothing."""
    org_part, _, secret = token.partition(".")
    try:
        org_id = uuid.UUID(hex=org_part)
    except ValueError:
        raise HTTPException(404, "Not found") from None
    if not 20 <= len(secret) <= 100:
        raise HTTPException(404, "Not found")
    db.set_context(org_id, None)
    proposal = db.scalar(select(Proposal).where(Proposal.token_hash == digest(token)))
    if proposal is None or proposal.status == "draft":
        raise HTTPException(404, "This proposal link is not valid.")
    return proposal, db.get(Organization, org_id), db.get(Customer, proposal.customer_id)


@router.get("/p/{token}")
def proposal_view(token: str, request: Request, db: TenantSession = Depends(get_db)):
    proposal, org, customer = _proposal(db, token)
    return render(request, "public/proposal.html", {"p": proposal, "org": org, "customer": customer,
                                                    "token": token, "error": ""})


@router.post("/p/{token}")
def proposal_decide(token: str, request: Request, db: TenantSession = Depends(get_db),
                    decision: str = Form(""), signer_name: str = Form("")):
    proposal, org, customer = _proposal(db, token)
    if rate_limited(db, "proposal:" + client_key(request), limit=20, window_seconds=3600):
        db.commit()
        db.set_context(org.id, None)
        raise HTTPException(429, "Too many attempts. Please try again later.")
    signer = _clean_line(signer_name, 120)
    if proposal.status != "sent":
        error = "This proposal has already been answered."
    elif decision not in ("approve", "decline"):
        error = "Choose approve or decline."
    elif len(signer) < 3:
        error = "Type your full name to confirm."
    else:
        proposal.status = "approved" if decision == "approve" else "declined"
        proposal.decided_name, proposal.decided_at, proposal.decided_revision = signer, now(), proposal.revision
        audit(db, org.id, None, "proposal." + proposal.status, "proposal", proposal.id,
              {"revision": proposal.revision, "via": "customer_link"})
        return RedirectResponse(f"/p/{token}", status_code=303)
    return render(request, "public/proposal.html", {"p": proposal, "org": org, "customer": customer,
                                                    "token": token, "error": error}, status_code=422)
