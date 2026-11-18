"""MF-086: owner-only Stripe customer portal with restricted PostgreSQL tenancy."""
from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Assessment, BillingSubscription, Membership, Organization, ServiceOrder, User, now
from app.security import hash_password
from app.services.billing_portal import BillingPortalError, create_portal_session, verified_customer_for

from .conftest import csrf_of, login, onboard_business


def _business(platform, slug):
    return onboard_business(
        platform, business="Portal " + slug + " " + uuid.uuid4().hex[:8],
        owner_email=slug + "-" + uuid.uuid4().hex[:10] + "@fictional.test",
    )


def _linked_paid_order(org_id, *, customer="cus_VERIFIED987654"):
    with session_scope(org_id=org_id) as db:
        order = db.scalar(select(ServiceOrder).where(ServiceOrder.org_id == org_id))
        assert order is not None
        order.offer_code = "operate"
        order.offer_snapshot = {"code": "operate", "billing": "monthly", "price_cents": 9900}
        order.amount_cents = 9900
        order.paid_at = now()
        order.status = "provisioned"
        order.payment_provider = "stripe"
        order.payment_evidence = "stripe:evt_verified"
        db.add(BillingSubscription(
            stripe_subscription_id="sub_" + uuid.uuid4().hex,
            stripe_customer_id=customer, org_id=org_id, order_id=order.id,
            status="active", invoice_status="paid", period_end=now() + timedelta(days=25),
        ))
        org = db.get(Organization, org_id)
        org.plan = "operate"  # synthetic paid monthly tenant, not the fixture's free consult
        state = dict(org.settings or {})
        state["billing"] = {"offer": "Ongoing workspace", "paid": True, "amount_cents": 9900}
        org.settings = state
    return customer


def test_verified_tenant_owner_only_post_csrf_and_free_beta(platform, monkeypatch):
    owner_a, org_a, _ = _business(platform, "a")
    owner_b, org_b, _ = _business(platform, "b")
    calls = []

    def fake(customer_id, *, return_url, stripe_secret_key):
        calls.append((customer_id, return_url))
        return "https://billing.stripe.com/p/session/testsession12345"

    monkeypatch.setattr("app.routers.billing_portal.billing_portal.create_portal_session", fake)
    monkeypatch.setenv("MF_PAYMENT_PROVIDER", "stripe")
    monkeypatch.setenv("MF_STRIPE_SECRET_KEY", "sk_test_syntheticonly")
    monkeypatch.setenv("MF_STRIPE_WEBHOOK_SECRET", "whsec_syntheticonly")
    monkeypatch.setenv("MF_PUBLIC_BASE_URL", "https://spectracleanse.com")
    get_settings.cache_clear()
    try:
        # Initially both are unpaid consult/ beta owners, no portal customer association.
        assert "Manage billing in Stripe" not in owner_a.get("/app/settings").text
        result = owner_a.post("/app/billing/portal", data={"csrf": csrf_of(owner_a)})
        assert result.status_code == 303 and "/app/settings" in result.headers["location"]
        assert not calls
        # Add one real-webhook-shaped customer for A only, not B.
        customer = _linked_paid_order(org_a)
        with session_scope() as db:
            assert verified_customer_for(db, org_a) == customer
            assert verified_customer_for(db, org_b) is None
        assert "Manage billing in Stripe" in owner_a.get("/app/settings").text
        assert "Manage billing in Stripe" not in owner_b.get("/app/settings").text

        # CSRF needed for any portal action.
        assert owner_a.post("/app/billing/portal").status_code == 403
        assert not calls
        response = owner_a.post("/app/billing/portal", data={"csrf": csrf_of(owner_a)})
        assert response.status_code == 303
        assert response.headers["location"].startswith("https://billing.stripe.com/p/session/")
        assert response.headers["referrer-policy"] == "no-referrer"
        assert calls == [(customer, "https://spectracleanse.com/app/settings")]
        response = owner_b.post("/app/billing/portal", data={"csrf": csrf_of(owner_b)})
        assert response.status_code == 303 and "/app/settings" in response.headers["location"]
        assert len(calls) == 1

        # A nonowner member cannot see the billing control or open the portal.
        address = "staff-portal-" + uuid.uuid4().hex[:8] + "@fictional.test"
        secret = "staff test passphrase 123"
        with session_scope(org_id=org_a) as db:
            person = User(email=address, name="Staff Tester", password_hash=hash_password(secret))
            db.add(person)
            db.flush()
            db.add(Membership(org_id=org_a, user_id=person.id, role="staff"))
        staff = login(platform, address, secret)
        assert "Manage billing in Stripe" not in staff.get("/app/settings").text
        assert staff.post("/app/billing/portal", data={"csrf": csrf_of(staff)}).status_code == 403
        admin_address = "admin-portal-" + uuid.uuid4().hex[:8] + "@fictional.test"
        with session_scope(org_id=org_a) as db:
            person = User(email=admin_address, name="Admin Tester", password_hash=hash_password(secret))
            db.add(person)
            db.flush()
            db.add(Membership(org_id=org_a, user_id=person.id, role="admin"))
        admin = login(platform, admin_address, secret)
        assert "Manage billing in Stripe" not in admin.get("/app/settings").text
        assert admin.post("/app/billing/portal", data={"csrf": csrf_of(admin)}).status_code == 403
        assert len(calls) == 1

        # Stripe cancellation revokes launch even if an old display snapshot says paid.
        with session_scope() as db:
            row = db.scalar(select(BillingSubscription).where(BillingSubscription.org_id == org_a))
            row.status = "canceled"
        with session_scope() as db:
            assert verified_customer_for(db, org_a) is None
        response = owner_a.post("/app/billing/portal", data={"csrf": csrf_of(owner_a)})
        assert response.status_code == 303 and "/app/settings" in response.headers["location"]
        assert len(calls) == 1
    finally:
        get_settings.cache_clear()


def test_conflicting_verified_stripe_customers_fail_closed(platform):
    owner, org_id, _ = _business(platform, "conflict")
    _linked_paid_order(org_id, customer="cus_FIRSTCUSTOMER123")
    with session_scope(org_id=org_id) as db:
        assessment = Assessment(
            token_hash=uuid.uuid4().hex * 2,
            business_name="Synthetic conflicting billing customer",
            contact_name="Test Buyer", email="conflict@fictional.test",
            website="", industry="other", excited_text="test", blocker_text="test",
            result={}, consent_at=now(),
        )
        db.add(assessment)
        db.flush()
        order = ServiceOrder(
            assessment_id=assessment.id, offer_code="operate",
            offer_snapshot={"code": "operate", "billing": "monthly", "price_cents": 9900},
            amount_cents=9900, status="provisioned",
            approved_name="Test Buyer", approved_at=now(), terms_version="test",
            payment_provider="stripe", payment_evidence="stripe:evt_other",
            paid_at=now(), org_id=org_id,
        )
        db.add(order)
        db.flush()
        db.add(BillingSubscription(
            stripe_subscription_id="sub_" + uuid.uuid4().hex,
            stripe_customer_id="cus_SECOND98765432",
            org_id=org_id, order_id=order.id,
            status="active", invoice_status="paid",
            period_end=now() + timedelta(days=15),
        ))
    with session_scope() as db:
        assert verified_customer_for(db, org_id) is None
    assert owner.get("/app/settings").status_code == 200


def test_portal_launch_rate_limited(platform, monkeypatch):
    owner, org_id, _ = _business(platform, "rate")
    _linked_paid_order(org_id)
    calls = []
    monkeypatch.setenv("MF_PAYMENT_PROVIDER", "stripe")
    monkeypatch.setenv("MF_STRIPE_SECRET_KEY", "sk_test_synthetic_no_charge")
    monkeypatch.setenv("MF_STRIPE_WEBHOOK_SECRET", "whsec_synthetic_no_charge")
    monkeypatch.setenv("MF_PUBLIC_BASE_URL", "https://spectracleanse.com")
    get_settings.cache_clear()

    def fake(customer_id, *, return_url, stripe_secret_key):
        calls.append(customer_id)
        return "https://billing.stripe.com/p/session/testsession12345"

    monkeypatch.setattr("app.routers.billing_portal.billing_portal.create_portal_session", fake)
    try:
        csrf = csrf_of(owner)
        for _ in range(10):
            response = owner.post("/app/billing/portal", data={"csrf": csrf})
            assert response.status_code == 303
            assert response.headers["location"].startswith("https://billing.stripe.com/")
        denied = owner.post("/app/billing/portal", data={"csrf": csrf})
        assert denied.status_code == 303
        assert denied.headers["location"].startswith("/app/settings")
        assert len(calls) == 10
    finally:
        get_settings.cache_clear()


def test_stripe_portal_api_allowlist_and_errors():
    observed = []

    def handler(request):
        observed.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api.stripe.com/v1/billing_portal/sessions"
        assert "customer=cus_VERIFIED987654" in request.content.decode()
        assert b"return_url=https%3A%2F%2Fspectracleanse.com%2Fapp%2Fsettings" in request.content
        return httpx.Response(200, json={"url": "https://billing.stripe.com/p/session/sess_123456789"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = create_portal_session(
            "cus_VERIFIED987654", stripe_secret_key="sk_test_synthetic_only",
            return_url="https://spectracleanse.com/app/settings", client=client,
        )
    assert result.endswith("sess_123456789") and len(observed) == 1

    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={"url": "https://billing.stripe.com.evil.test/p/session/steal"}))) as client:
        with pytest.raises(BillingPortalError):
            create_portal_session("cus_VERIFIED987654", stripe_secret_key="sk_test_synthetic_only",
                                  return_url="https://spectracleanse.com/app/settings", client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={"url": "https://billing.stripe.com/p/session/valid?redirect=evil"}))) as client:
        with pytest.raises(BillingPortalError):
            create_portal_session("cus_VERIFIED987654", stripe_secret_key="sk_test_synthetic_only",
                                  return_url="https://spectracleanse.com/app/settings", client=client)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(
            503, json={"error": "fake"}))) as client:
        with pytest.raises(BillingPortalError):
            create_portal_session("cus_VERIFIED987654", stripe_secret_key="sk_test_synthetic_only",
                                  return_url="https://spectracleanse.com/app/settings", client=client)
    with pytest.raises(BillingPortalError):
        create_portal_session("cus_bad", stripe_secret_key="sk_test_synthetic_only",
                              return_url="https://spectracleanse.com/app/settings")
    with pytest.raises(BillingPortalError):
        create_portal_session("cus_VERIFIED987654", stripe_secret_key="sk_test_synthetic_only",
                              return_url="http://localhost/app/settings")
