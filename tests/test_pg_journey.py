"""End-to-end customer journey on real PostgreSQL, with fictional businesses.

Covers the fifteen-step journey from the build brief: visit -> two questions -> assessment
-> choose a service -> approval (no payment simulated) -> organization -> workspace ->
website -> preview/approve/publish -> real inquiry through the published site -> correct
workspace -> respond/assign -> recorded evidence -> implemented vs. measurable.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from .conftest import client_for, create_and_publish_site, csrf_of, onboard_business


def test_public_pages_render(platform):
    visitor = client_for(platform)
    home = visitor.get("/")
    assert home.status_code == 200
    assert "most excited about" in home.text and "next level" in home.text
    assert "Content-Security-Policy" in home.headers
    for path in ("/assessment", "/privacy", "/terms", "/login", "/forgot"):
        assert visitor.get(path).status_code == 200, path
    assert visitor.get("/app").status_code == 303  # workspace requires sign-in
    assert visitor.get("/operator").status_code == 303


def test_assessment_is_honest_about_scope_and_evidence(platform):
    visitor = client_for(platform)
    response = visitor.post("/assessment", data={
        "excited": "Our catering side is growing and I love the corporate lunch customers.",
        "blocker": "Honestly cash flow is tight and I can't get a loan to buy a second van.",
        "business_name": "Fictional Fork Catering", "contact_name": "Casey Cook",
        "email": "casey@fictionalfork.example", "industry": "food", "consent": "yes",
        "website": "fictionalfork.example"})
    assert response.status_code == 303
    page = visitor.get(response.headers["location"]).text
    assert "Outside what we do" in page                      # money problems are not oversold
    assert "did not visit or scan" in page                    # the URL was stored, never fetched
    assert "do not give financial advice" in page             # says so plainly
    assert "not a package" in page                            # recommends a conversation instead
    assert "No payment is taken" in page                      # payments are off in this environment

    # Validation: short answers and missing consent are rejected with a helpful page.
    bad = visitor.post("/assessment", data={"excited": "x", "blocker": "y", "business_name": "",
                                            "contact_name": "", "email": "nope"})
    assert bad.status_code == 422 and "Please check" in bad.text


def test_full_customer_journey(platform):
    from app.db import session_scope
    from app.models import AuditEvent, EmailMessage, Improvement, Lead, ServiceOrder

    owner, org_id, token = onboard_business(
        platform, business="Canyon Test Cleaning", owner_email="owner@canyontest.example")

    # Step 6: approval is recorded, and nothing claims a payment happened.
    with session_scope() as db:
        order = db.scalar(select(ServiceOrder).where(ServiceOrder.org_id == org_id))
        assert order.paid_at is None and order.payment_evidence.startswith("NOT PAID")
        invite = db.scalar(select(EmailMessage).where(EmailMessage.kind == "invite",
                                                      EmailMessage.org_id == org_id))
        assert invite.status == "not_sent_no_provider"        # never reported as delivered

    # Step 8: the workspace starts with improvements derived from the owner's own answers.
    dashboard = owner.get("/app")
    assert "Canyon Test Cleaning" in dashboard.text
    improvements = owner.get("/app/improvements").text
    assert "You told us" in improvements and "deep cleaning" in improvements

    # Steps 9-10: build, preview, version, approve, publish.
    host, version_id = create_and_publish_site(owner, name="Canyon Test Cleaning", slug="canyon-test")
    site = client_for(platform, host)
    page = site.get("/")
    assert page.status_code == 200 and "Canyon Test Cleaning" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert site.get("/style.css").status_code == 200 and site.get("/site.js").status_code == 200
    assert site.get("/app").status_code == 404                # no private app on a customer host
    assert site.get("/login").status_code == 404

    # Step 11: a customer submits a real inquiry through the published site.
    site.post("/api/event", json={"kind": "page_view"})
    site.post("/api/event", json={"kind": "form_start"})
    sent = site.post("/api/lead", json={
        "name": "Riley Resident", "email": "riley@resident.example", "phone": "555-010-0142",
        "service": "Deep cleaning", "timing": "this_month",
        "message": "Could you quote a deep clean for a three bedroom house?"})
    assert sent.status_code == 201, sent.text

    # Step 12: it reaches the right workspace.
    inbox = owner.get("/app/inquiries").text
    assert "Riley Resident" in inbox
    lead_id = re.search(r"/app/inquiries/([0-9a-f-]{36})", inbox).group(1)
    detail = owner.get(f"/app/inquiries/{lead_id}")
    assert "three bedroom house" in detail.text

    # Step 13: the owner responds, assigns and schedules a follow-up.
    csrf = csrf_of(owner)
    me = owner.get("/api/v1/me").json()
    updated = owner.post(f"/app/inquiries/{lead_id}/update", data={
        "csrf": csrf, "status": "contacted", "assigned_to": me["user"]["id"], "follow_up_on": "2026-12-01"})
    assert updated.status_code == 303
    assert owner.post(f"/app/inquiries/{lead_id}/note", data={"csrf": csrf, "body": "Called, sending a quote."}).status_code == 303
    converted = owner.post(f"/app/inquiries/{lead_id}/convert", data={"csrf": csrf})
    assert converted.status_code == 303 and "/app/customers/" in converted.headers["location"]

    # Step 14: meaningful actions are recorded; the notification carries no inquiry content.
    with session_scope(org_id=org_id) as db:
        actions = set(db.scalars(select(AuditEvent.action)))
        assert {"org.provisioned", "site.published", "lead.received", "lead.updated", "lead.converted"} <= actions
        lead = db.get(Lead, lead_id)
        assert lead.first_response_at is not None and lead.status in ("contacted", "qualified")
        notice = db.scalar(select(EmailMessage).where(EmailMessage.kind == "lead_notice",
                                                      EmailMessage.org_id == org_id))
        assert notice is not None and "three bedroom" not in notice.body and "Riley" not in notice.body
        website = db.scalar(select(Improvement).where(Improvement.capability == "website"))
        # Step 15: implemented vs. measurable, with an honest evidence label.
        assert website.status == "implemented" and website.evidence_status == "system_recorded"

    insights = owner.get("/app/insights").text
    assert "Search Console" in insights and "Not connected" in insights
    activity = owner.get("/app/activity").text
    assert "site: published" in activity


def test_versions_rollback_and_unpublish(platform):
    owner, org_id, _ = onboard_business(platform, business="Rollback Test Co", owner_email="owner@rollback.example")
    host, v1 = create_and_publish_site(owner, name="Rollback Test Co", slug="rollback-test")
    site = client_for(platform, host)
    csrf = csrf_of(owner)
    assert owner.post("/app/website/save", data={
        "csrf": csrf, "name": "Rollback Test Co", "city": "Fictionville, AZ", "industry": "cleaning",
        "color": "copper", "email": "hi@rollback.example", "phone": "", "about": "", "headline": "Brand new headline",
        "intro": "", "response_window": "no_promise", "services": "Only service"}).status_code == 303
    # A saved draft does not change what visitors see.
    assert "Brand new headline" not in site.get("/").text
    assert owner.post("/app/website/version", data={"csrf": csrf, "note": "v2"}).status_code == 303
    page = owner.get("/app/website").text
    v2 = re.search(r"/app/website/versions/([0-9a-f-]{36})/approve", page).group(1)
    # Publishing an unapproved version is refused.
    owner.post(f"/app/website/versions/{v2}/publish", data={"csrf": csrf})
    assert "Brand new headline" not in site.get("/").text
    owner.post(f"/app/website/versions/{v2}/approve", data={"csrf": csrf, "confirm_approve": "yes"})
    owner.post(f"/app/website/versions/{v2}/publish", data={"csrf": csrf, "confirm_publish": "yes"})
    assert "Brand new headline" in site.get("/").text
    # Roll back to version 1.
    owner.post(f"/app/website/versions/{v1}/publish", data={"csrf": csrf, "confirm_publish": "yes"})
    assert "Brand new headline" not in site.get("/").text
    owner.post("/app/website/unpublish", data={"csrf": csrf, "confirm_unpublish": "yes"})
    assert site.get("/").status_code == 404
    assert site.post("/api/lead", json={"name": "A", "email": "a@b.example", "message": "hello"}).status_code == 404


def test_proposal_approval_link(platform):
    from app.db import session_scope
    from app.models import Proposal
    owner, org_id, _ = onboard_business(platform, business="Proposal Test Co", owner_email="owner@proposal.example")
    csrf = csrf_of(owner)
    added = owner.post("/app/customers", data={"csrf": csrf, "name": "Pat Customer", "email": "pat@customer.example"})
    customer_path = added.headers["location"].split("?")[0]
    assert owner.post(customer_path + "/proposals", data={
        "csrf": csrf, "title": "Deep clean", "scope": "Whole house", "items": "Deep clean | 1 | 240\nOven | 2 | 35.50"
    }).status_code == 303
    with session_scope(org_id=org_id) as db:
        proposal = db.scalar(select(Proposal))
        assert proposal.total_cents == 24000 + 7100 and proposal.status == "draft"
        proposal_id = proposal.id
    page = owner.post(f"/app/proposals/{proposal_id}/send", data={"csrf": csrf})
    link = re.search(r"/p/[0-9a-f]{32}\.[A-Za-z0-9_\-]+", page.text).group(0)
    assert "We have not sent anything" in page.text

    customer = client_for(platform)
    assert "$311.00" in customer.get(link).text
    forged = "/p/" + "0" * 32 + "." + link.split(".", 1)[1]
    assert customer.get(forged).status_code == 404
    assert customer.post(link, data={"decision": "approve", "signer_name": ""}).status_code == 422
    assert customer.post(link, data={"decision": "approve", "signer_name": "Pat Customer"}).status_code == 303
    assert customer.post(link, data={"decision": "decline", "signer_name": "Pat Customer"}).status_code == 422
    with session_scope(org_id=org_id) as db:
        proposal = db.get(Proposal, proposal_id)
        assert proposal.status == "approved" and proposal.decided_name == "Pat Customer"


def test_website_publish_actions_require_server_side_confirmation(platform):
    """Client-side required checkboxes alone must not authorize a direct POST."""
    owner, _, _ = onboard_business(
        platform,
        business="Explicit Confirm Roofing",
        owner_email="owner@explicit-confirm.example",
    )
    hostname, version_id = create_and_publish_site(
        owner, name="Explicit Confirm Roofing", slug="explicit-confirm-roofing",
    )
    csrf = csrf_of(owner)
    live = client_for(platform, hostname)
    assert live.get("/").status_code == 200

    # A valid owner session and CSRF token are not substitutes for informed confirmation.
    routes = (
        (f"/app/website/versions/{version_id}/approve", "confirm_approve"),
        (f"/app/website/versions/{version_id}/publish", "confirm_publish"),
        ("/app/website/unpublish", "confirm_unpublish"),
    )
    for path, key in routes:
        assert owner.post(path, data={"csrf": csrf}).status_code == 403
        assert owner.post(path, data={"csrf": csrf, key: "no"}).status_code == 403
        assert live.get("/").status_code == 200
    assert owner.post("/app/website/unpublish", data={
        "csrf": csrf, "confirm_unpublish": "yes",
    }).status_code == 303
    assert live.get("/").status_code == 404
