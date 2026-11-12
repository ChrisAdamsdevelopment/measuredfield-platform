"""Existing-business Growth Partner checkout never creates a second workspace.

Only fake Stripe Checkout and locally signed Stripe webhook requests are used.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx
from sqlalchemy import func, select

from app.config import DEFAULT_OFFERS, get_settings
from .conftest import client_for, csrf_of, onboard_business

SECRET = "whsec_growth_partner_synthetic"
OFFER = {
    "code": "growth_partner",
    "name": "MeasuredField Growth Partner",
    "price_cents": 9900,
    "billing": "monthly",
    "add_on": True,
    "summary": "Optional hands-on strategy and marketing support.",
    "includes": ["One defined monthly working session."],
    "stripe_price_id": "price_SyntheticGrowthPartner99",
    "allow_promotion_codes": True,
    "promotion_code": "GUIDEDLAUNCH",
    "promotion_disclosure": (
        "Growth Partner first month is free, then $99/month unless cancelled. "
        "Business Workspace is billed separately."
    ),
}


def configured(monkeypatch):
    from app.services import payments

    settings = get_settings()
    old = settings.offers_json
    settings.offers_json = json.dumps([*DEFAULT_OFFERS, OFFER])
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "cs_test_" + uuid.uuid4().hex,
                                         "url": "https://checkout.stripe.com/fake-growth-checkout"})

    provider = payments.StripeProvider(
        "sk_test_synthetic", SECRET,
        client=httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(payments, "get_provider", lambda settings=None: provider)
    return settings, old, requests


def signed(client, data):
    from app.services.payments import sign_for_test
    blob = json.dumps(data).encode("utf-8")
    return client.post("/webhooks/stripe", content=blob,
                       headers={"Stripe-Signature": sign_for_test(SECRET, blob, int(time.time()))})


def synthetic_event(kind, obj, created):
    return {"id": "evt_test_" + uuid.uuid4().hex, "type": kind, "created": created,
            "data": {"object": obj}}


def test_growth_partner_offer_hidden_from_public_and_billing_works_for_owner(platform, monkeypatch):
    from app.db import session_scope
    from app.models import Organization, ServiceOrder, BillingSubscription
    from app.services.orders import active_subscription_for

    owner, org_id, assessment_token = onboard_business(
        platform, business="Synthetic Existing Cleaning",
        owner_email="owner-growth-" + uuid.uuid4().hex[:10] + "@example.test")
    settings, old, requests = configured(monkeypatch)
    try:
        public_page = client_for(platform).get("/")
        assert public_page.status_code == 200
        assert "Growth Partner" not in public_page.text  # not a stand-alone new-workspace offer
        assess_page = client_for(platform).get("/assessment/r/" + assessment_token)
        assert assess_page.status_code == 200
        assert "MeasuredField Growth Partner" not in assess_page.text

        screen = owner.get("/app/billing")
        assert screen.status_code == 200, screen.text[:600]
        assert "GUIDEDLAUNCH" in screen.text
        assert "Business Workspace is billed separately." in screen.text
        assert 'data-copy-promo="GUIDEDLAUNCH"' in screen.text
        assert owner.get("/app/settings").status_code == 200

        # Request requires authenticated owner + valid CSRF + checked consent.
        no_csrf = owner.post("/app/billing/growth-partner", data={"agree": "yes"})
        assert no_csrf.status_code == 403
        no_consent = owner.post("/app/billing/growth-partner", data={"csrf": csrf_of(owner)})
        assert no_consent.status_code == 303
        assert not requests

        checkout = owner.post("/app/billing/growth-partner",
                              data={"csrf": csrf_of(owner), "agree": "yes"})
        assert checkout.status_code == 303 and "checkout.stripe.com" in checkout.headers["location"]
        assert len(requests) == 1
        assert b"allow_promotion_codes=true" in requests[0].content
        with session_scope() as db:
            from sqlalchemy import select
            order = db.scalar(select(ServiceOrder).where(
                ServiceOrder.offer_code == "growth_partner").order_by(
                ServiceOrder.created_at.desc()))
            assert order and order.org_id == org_id and order.status == "payment_pending"
            sid = order.provider_ref
            oid = order.id
            assert db.scalar(select(func.count()).select_from(Organization)) == 0  # RLS with no org
        # Same org's second attempt is blocked without creating another Checkout.
        repeated = owner.post("/app/billing/growth-partner",
                              data={"csrf": csrf_of(owner), "agree": "yes"})
        assert repeated.status_code == 303 and len(requests) == 1

        stamp = int(time.time())
        stripe_sub = "sub_growth_" + uuid.uuid4().hex
        customer = "cus_growth_" + uuid.uuid4().hex
        subscription = {"id": stripe_sub, "customer": customer,
                        "status": "active", "current_period_end": stamp + 86400 * 30,
                        "metadata": {"order_id": str(oid)}}
        created = signed(owner, synthetic_event("customer.subscription.created", subscription, stamp + 1))
        assert created.status_code == 200 and created.json()["outcome"] == "subscription_updated"
        invoice = {"id": "in_growth_" + uuid.uuid4().hex,
                   "subscription": stripe_sub, "customer": customer}
        assert signed(owner, synthetic_event("invoice.paid", invoice, stamp + 2)).json()[
            "outcome"] == "invoice_paid"
        completed = {"id": sid, "customer": customer, "subscription": stripe_sub,
                     "payment_status": "no_payment_required", "mode": "subscription",
                     "currency": "usd", "amount_subtotal": 9900, "amount_total": 0,
                     "total_details": {"amount_discount": 9900, "amount_tax": 0}}
        ok = signed(owner, synthetic_event("checkout.session.completed", completed, stamp + 3))
        assert ok.status_code == 200 and ok.json()["outcome"] == "paid", ok.text

        with session_scope() as db:
            row = db.get(ServiceOrder, oid)
            assert row.org_id == org_id and row.status == "provisioned"
            assert row.settled_amount_cents == 0
            assert db.scalar(select(BillingSubscription).where(
                BillingSubscription.order_id == oid)).org_id == org_id
            assert active_subscription_for(db, org_id, "growth_partner")
        with session_scope(org_id=org_id) as db:
            assert db.scalar(select(func.count()).select_from(Organization)) == 1
        assert "Active" in owner.get("/app/billing").text
        assert len(requests) == 1
    finally:
        settings.offers_json = old


def test_growth_checkout_disabled_by_default_and_not_a_new_assessment_offer(platform):
    from app.services.orders import create_order, OrderError
    from app.models import Assessment
    from app.db import session_scope

    owner, org_id, _ = onboard_business(
        platform, business="Disabled Growth Co",
        owner_email="disabled-growth-" + uuid.uuid4().hex[:10] + "@example.test")
    screen = owner.get("/app/billing")
    assert screen.status_code == 200
    assert "Not available yet" in screen.text
    assert "data-copy-promo=" not in screen.text
    assert owner.post("/app/billing/growth-partner",
                      data={"csrf": csrf_of(owner), "agree": "yes"}).status_code == 303
    settings = get_settings()
    original = settings.offers_json
    settings.offers_json = json.dumps([*DEFAULT_OFFERS, OFFER])
    try:
        with session_scope() as db:
            row = db.scalar(select(Assessment).order_by(Assessment.created_at.desc()))
            try:
                create_order(db, row, "growth_partner", "Owner Person")
                assert False, "add-on must not produce a new standalone workspace"
            except OrderError:
                pass
    finally:
        settings.offers_json = original
