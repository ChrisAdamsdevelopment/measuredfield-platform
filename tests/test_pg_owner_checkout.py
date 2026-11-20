"""MF-091: owner-bound zero-dollar founder Checkout through the REAL application.

All Stripe activity is synthetic via fake checkout API plus locally signed webhook
payloads. Requires disposable PostgreSQL; never talks to a real provider.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import BillingSubscription, Organization, ServiceOrder, now
from app.services.orders import active_subscription_for, pending_owner_checkout
from app.services.payments import sign_for_test

from .conftest import csrf_of, login, onboard_business

SIGNATURE_SECRET = "whsec_owner_checkout_synthetic"


def _owner(platform, tag):
    return onboard_business(
        platform, business="Synthetic Founder " + tag + " " + uuid.uuid4().hex[:6],
        owner_email=tag + "-" + uuid.uuid4().hex[:9] + "@fictional.test",
    )


@pytest.fixture
def billing_mode(monkeypatch):
    offers = [
        {"code": "consult", "name": "Working session", "price_cents": 0, "billing": "free",
         "includes": [], "summary": "Synthetic onboarding"},
        {"code": "operate", "name": "Ongoing workspace", "price_cents": 9900,
         "billing": "monthly", "stripe_price_id": "price_syntheticonly12345",
         "allow_promotion_codes": True, "includes": [], "summary": "For testing"},
    ]
    monkeypatch.setenv("MF_PAYMENT_PROVIDER", "stripe")
    monkeypatch.setenv("MF_STRIPE_SECRET_KEY", "sk_test_ownercheckout_dummy")
    monkeypatch.setenv("MF_STRIPE_WEBHOOK_SECRET", SIGNATURE_SECRET)
    monkeypatch.setenv("MF_PUBLIC_BASE_URL", "https://spectracleanse.com")
    monkeypatch.setenv("MF_OFFERS_JSON", json.dumps(offers))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _webhook(client, kind, obj, suffix):
    event = {"id": "evt_synthetic_" + suffix, "type": kind, "created": int(time.time()),
             "data": {"object": obj}}
    body = json.dumps(event, separators=(",", ":")).encode()
    signature = sign_for_test(SIGNATURE_SECRET, body, event["created"])
    return client.post("/webhooks/stripe", content=body, headers={
        "stripe-signature": signature, "content-type": "application/json"})


def test_founder_discount_checkout_reuses_owner_workspace(platform, billing_mode, monkeypatch):
    owner, org_id, _ = _owner(platform, "sameorg")
    with session_scope(org_id=org_id) as db:
        org = db.get(Organization, org_id)
        org.plan = "founding_beta"
        state = dict(org.settings)
        state["billing"] = {"offer": "Founding beta", "paid": False, "amount_cents": 0}
        org.settings = state
    seen = []

    class FakeStripe:
        def create_checkout(self, *, order_id, offer, customer_email, success_url, cancel_url):
            seen.append({"order": order_id, "offer": offer, "email": customer_email,
                         "success_url": success_url, "cancel_url": cancel_url})
            return {"id": "cs_test_owner_" + order_id.replace("-", ""),
                    "url": "https://checkout.stripe.com/c/pay/cs_test_" + order_id.replace("-", "")}

    assert "Open Business Workspace checkout" in owner.get("/app/settings").text
    with monkeypatch.context() as m:
        m.setattr("app.routers.owner_checkout.payments.get_provider", lambda settings=None: FakeStripe())
        m.setattr("app.routers.owner_checkout._inspect_stripe_checkout",
                  lambda provider, order: {"status": "open", "url":
                      "https://checkout.stripe.com/c/pay/cs_test_" +
                      str(order.id).replace("-", "")})
        response = owner.post("/app/billing/checkout", data={"csrf": csrf_of(owner)})
        assert response.status_code == 303
        assert response.headers["location"].startswith("https://checkout.stripe.com/")
        second = owner.post("/app/billing/checkout", data={"csrf": csrf_of(owner)})
        assert second.status_code == 303
        assert second.headers["location"] == response.headers["location"]
    assert len(seen) == 1
    assert seen[0]["offer"]["allow_promotion_codes"] is True
    assert seen[0]["offer"]["stripe_price_id"] == "price_syntheticonly12345"
    assert seen[0]["offer"]["checkout_origin"] == "owner_workspace"
    assert seen[0]["success_url"] == "https://spectracleanse.com/app/settings"
    assert "Pending" in owner.get("/app/settings").text

    with session_scope(org_id=org_id) as db:
        order = db.scalar(select(ServiceOrder).where(ServiceOrder.assessment_id.is_(None),
                                                    ServiceOrder.org_id == org_id))
        assert order is not None and order.status == "payment_pending"
        assert order.provider_ref == "cs_test_owner_" + str(order.id).replace("-", "")
        assert pending_owner_checkout(db, org_id).id == order.id
        assert not active_subscription_for(db, org_id, "operate")
        order_id = str(order.id)
        checkout_id = order.provider_ref

    checkout = {
        "id": checkout_id, "payment_status": "no_payment_required",
        "amount_subtotal": 9900, "amount_total": 0,
        "total_details": {"amount_discount": 9900, "amount_tax": 0},
        "mode": "subscription", "currency": "usd",
        "customer": "cus_syntheticFounder123", "subscription": "sub_syntheticFounder123",
    }
    # An unknown session cannot hijack another owner's Checkout even if it
    # presents the same-looking amount, price and current org in the browser.
    wrong = _webhook(owner, "checkout.session.completed",
                     {**checkout, "id": "cs_test_unknown"}, "unknown")
    assert wrong.status_code == 200 and wrong.json()["outcome"] == "unknown_order"
    with session_scope(org_id=org_id) as db:
        assert not active_subscription_for(db, org_id, "operate")

    paid = _webhook(owner, "checkout.session.completed", checkout, "valid_checkout")
    assert paid.status_code == 200 and paid.json()["outcome"] == "paid"
    duplicate = _webhook(owner, "checkout.session.completed", checkout, "valid_checkout")
    assert duplicate.json()["outcome"] == "duplicate"

    with session_scope(org_id=org_id) as db:
        assert not active_subscription_for(db, org_id, "operate")
        order = db.get(ServiceOrder, uuid.UUID(order_id))
        assert order.paid_at is not None
        assert order.settled_amount_cents == 0
        assert order.discount_amount_cents == 9900
        assert order.org_id == org_id and order.assessment_id is None

    sub_obj = {
        "id": "sub_syntheticFounder123", "customer": "cus_syntheticFounder123",
        "metadata": {"order_id": order_id},
        "status": "active", "current_period_end": int((now()+timedelta(days=30)).timestamp()),
    }
    updated = _webhook(owner, "customer.subscription.updated", sub_obj, "subscription")
    assert updated.status_code == 200
    assert updated.json()["outcome"] == "subscription_updated"

    inv_obj = {
        "id": "in_syntheticFounders", "customer": "cus_syntheticFounder123",
        "parent": {"subscription_details": {
            "subscription": "sub_syntheticFounder123", "metadata": {"order_id": order_id}}},
    }
    paid_inv = _webhook(owner, "invoice.paid", inv_obj, "invoice")
    assert paid_inv.json()["outcome"] == "invoice_paid"

    with session_scope(org_id=org_id) as db:
        assert active_subscription_for(db, org_id, "operate")
        assert db.scalar(select(func.count()).select_from(Organization).where(
            Organization.id == org_id)) == 1
        sub = db.scalar(select(BillingSubscription).where(BillingSubscription.org_id == org_id))
        assert sub is not None and sub.order_id == uuid.UUID(order_id)
        assert sub.entitlement_active
        org = db.get(Organization, org_id)
        assert org.plan == "operate", "Signed owner checkout must upgrade same workspace plan"
        billing = org.settings["billing"]
        assert billing["paid"] is True and billing["order_id"] == order_id
        assert billing["amount_cents"] == 0 and billing["discount_cents"] == 9900
        assert org.settings["owner_checkout_previous_plan"] == "founding_beta"

    dashboard = owner.get("/app")
    assert dashboard.status_code == 200
    settings = owner.get("/app/settings")
    assert "verified Business Workspace subscription is active" in settings.text
    assert "Manage billing in Stripe" in settings.text
    assert "Open Business Workspace checkout" not in settings.text

    cancel = _webhook(owner, "customer.subscription.deleted",
                      {**sub_obj, "status": "canceled"}, "cancel")
    assert cancel.status_code == 200
    with session_scope(org_id=org_id) as db:
        assert not active_subscription_for(db, org_id, "operate")
        assert pending_owner_checkout(db, org_id) is None
        org = db.get(Organization, org_id)
        assert org.plan == "founding_beta"
        assert org.settings["billing"]["paid"] is False
        assert org.settings["billing"]["offer"] == "Founding beta"
    assert "Open Business Workspace checkout" in owner.get("/app/settings").text


def test_checkout_rejects_unauthorized_staff_cross_org_and_bad_url(platform, billing_mode, monkeypatch):
    alice, org_a, _ = _owner(platform, "alice")
    bob, org_b, _ = _owner(platform, "bob")
    assert alice.post("/app/billing/checkout").status_code == 403
    assert alice.post("/app/billing/checkout", data={"csrf": "forged"}).status_code == 403

    seen = []
    class InvalidStripe:
        def create_checkout(self, **kwargs):
            seen.append(kwargs["order_id"])
            return {"id": "cs_test_wrong", "url": "https://checkout.stripe.example.evil/c/pay"}
    with monkeypatch.context() as m:
        m.setattr("app.routers.owner_checkout.payments.get_provider", lambda settings=None: InvalidStripe())
        bad = alice.post("/app/billing/checkout", data={"csrf": csrf_of(alice),
                                                       "org_id": str(org_b)})
        assert bad.status_code == 303 and "/app/settings" in bad.headers["location"]
    assert len(seen) == 1
    with session_scope(org_id=org_a) as db:
        failed = db.scalar(select(ServiceOrder).where(
            ServiceOrder.assessment_id.is_(None), ServiceOrder.org_id == org_a))
        assert failed.status == "cancelled" and failed.provider_ref is None
    with session_scope(org_id=org_b) as db:
        assert db.scalar(select(ServiceOrder).where(
            ServiceOrder.assessment_id.is_(None), ServiceOrder.org_id == org_b)) is None
    assert bob.get("/app").status_code == 200

    # An administrative member can edit workspace settings but cannot start billing.
    from app.models import Membership, User
    from app.security import hash_password
    address = "staff-" + uuid.uuid4().hex[:9] + "@fictional.test"
    pw = "synthetic-test-password-123"
    with session_scope(org_id=org_a) as db:
        person = User(email=address, name="Staff synthetic", password_hash=hash_password(pw))
        db.add(person)
        db.flush()
        db.add(Membership(org_id=org_a, user_id=person.id, role="staff"))
    staff = login(platform, address, pw)
    assert "Open Business Workspace checkout" not in staff.get("/app/settings").text
    assert staff.post("/app/billing/checkout", data={"csrf": csrf_of(staff)}).status_code == 403


def test_expired_checkout_event_unblocks_exact_original_and_rejects_late_expiration(
        platform, billing_mode, monkeypatch):
    """No second Stripe subscription until signed expiration or verified provider status."""
    owner, org_id, _ = _owner(platform, "expired")
    calls = []

    class FakeStripe:
        def create_checkout(self, *, order_id, **kwargs):
            calls.append(order_id)
            return {
                "id": "cs_test_owner_" + order_id.replace("-", ""),
                "url": "https://checkout.stripe.com/c/pay/cs_test_" + order_id.replace("-", ""),
            }

        def verify_webhook(self, payload, signature_header):
            from app.services.payments import StripeProvider
            return StripeProvider("sk_test_synthetic", SIGNATURE_SECRET).verify_webhook(
                payload, signature_header)

    with monkeypatch.context() as m:
        m.setattr("app.routers.owner_checkout.payments.get_provider", lambda settings=None: FakeStripe())
        m.setattr("app.routers.owner_checkout._inspect_stripe_checkout",
                  lambda provider, order: None)
        response = owner.post("/app/billing/checkout", data={"csrf": csrf_of(owner)})
        assert response.status_code == 303
        assert response.headers["location"].startswith("https://checkout.stripe.com/")
        # Never bypass on local time alone.
        with session_scope(org_id=org_id) as db:
            pending = db.scalar(select(ServiceOrder).where(
                ServiceOrder.assessment_id.is_(None), ServiceOrder.org_id == org_id))
            old_id = pending.id
            old_session = pending.provider_ref
            pending.created_at = now() - timedelta(days=4)
        waiting = owner.post("/app/billing/checkout", data={"csrf": csrf_of(owner)})
        assert waiting.status_code == 303 and "/app/settings" in waiting.headers["location"]
        assert len(calls) == 1
        assert "Unable to verify your previous Stripe checkout" in owner.get(
            waiting.headers["location"]).text or waiting.headers["location"].startswith("/app/settings")

        # Signed expired event names exactly the bound provider session, not a browser ID.
        unbound = _webhook(owner, "checkout.session.expired",
                           {"id": "cs_test_not_a_session"}, "unknown_expire")
        assert unbound.json()["outcome"] == "unknown_order"
        with session_scope(org_id=org_id) as db:
            assert db.get(ServiceOrder, old_id).status == "payment_pending"
        expired = _webhook(owner, "checkout.session.expired",
                           {"id": old_session}, "real_expire")
        assert expired.json()["outcome"] == "checkout_expired"
        duplicate = _webhook(owner, "checkout.session.expired",
                             {"id": old_session}, "real_expire")
        assert duplicate.json()["outcome"] == "duplicate"
        with session_scope(org_id=org_id) as db:
            assert db.get(ServiceOrder, old_id).status == "cancelled"
            assert pending_owner_checkout(db, org_id) is None
        fresh = owner.post("/app/billing/checkout", data={"csrf": csrf_of(owner)})
        assert fresh.status_code == 303 and fresh.headers["location"].startswith(
            "https://checkout.stripe.com/")
        assert fresh.headers["location"] != response.headers["location"]
        assert len(calls) == 2
        late = _webhook(owner, "checkout.session.expired",
                        {"id": old_session}, "late_expire")
        assert late.json()["outcome"] == "not_pending"
        with session_scope(org_id=org_id) as db:
            assert db.scalar(select(func.count()).select_from(Organization).where(
                Organization.id == org_id)) == 1
            assert db.scalar(select(func.count()).select_from(ServiceOrder).where(
                ServiceOrder.org_id == org_id, ServiceOrder.assessment_id.is_(None))) == 2


def test_open_checkout_verified_by_stripe_is_resumed_not_duplicated(
        platform, billing_mode, monkeypatch):
    owner, org_id, _ = _owner(platform, "resume")
    session_requests = []
    class FakeStripe:
        def create_checkout(self, *, order_id, **kwargs):
            session_requests.append(order_id)
            return {"id": "cs_test_owner_" + order_id.replace("-", ""),
                    "url": "https://checkout.stripe.com/c/pay/cs_test_" + order_id.replace("-", "")}
    with monkeypatch.context() as m:
        m.setattr("app.routers.owner_checkout.payments.get_provider", lambda settings=None: FakeStripe())
        m.setattr("app.routers.owner_checkout._inspect_stripe_checkout",
                  lambda provider, order: {"status": "expired"})
        first = owner.post("/app/billing/checkout", data={"csrf": csrf_of(owner)})
        with session_scope(org_id=org_id) as db:
            old = db.scalar(select(ServiceOrder).where(
                ServiceOrder.org_id == org_id, ServiceOrder.assessment_id.is_(None)))
            old_id = old.id
        next_ = owner.post("/app/billing/checkout", data={"csrf": csrf_of(owner)})
    assert first.status_code == 303 and next_.status_code == 303
    assert len(session_requests) == 2
    assert first.headers["location"] != next_.headers["location"]
    with session_scope(org_id=org_id) as db:
        assert db.get(ServiceOrder, old_id).status == "cancelled"
