"""MF-006: signed Stripe promotions and subscription reconciliation on real PostgreSQL.

No network or real money: all Checkout requests use MockTransport, and all
webhook payloads are fictitious and locally signed.
"""
from __future__ import annotations

import json
import time
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from .conftest import client_for

SECRET = "whsec_test_only_fictional"
PRICE = "price_UnitTestGrowth99"


@pytest.fixture()
def stripe(monkeypatch):
    from app.services import payments

    calls = []

    def mock_send(request: httpx.Request):
        calls.append(request)
        return httpx.Response(200, json={
            "id": "cs_test_" + uuid.uuid4().hex,
            "url": "https://checkout.stripe.com/test/fake"})

    provider = payments.StripeProvider(
        "sk_test_fictional", SECRET,
        client=httpx.Client(transport=httpx.MockTransport(mock_send)))
    monkeypatch.setattr(payments, "get_provider", lambda settings=None: provider)
    return provider, calls


def event(kind, obj, created, event_id=None):
    return {
        "id": event_id or "evt_test_" + uuid.uuid4().hex,
        "type": kind,
        "created": created,
        "data": {"object": obj},
    }


def signed(client, data):
    from app.services.payments import sign_for_test

    payload = json.dumps(data).encode("utf-8")
    return client.post("/webhooks/stripe", content=payload, headers={
        "content-type": "application/json",
        "stripe-signature": sign_for_test(SECRET, payload, int(time.time()))})


def recurring_order(app):
    from app.db import session_scope
    from app.models import ServiceOrder

    client = client_for(app)
    email = "founding-" + uuid.uuid4().hex[:10] + "@example.test"
    result = client.post("/assessment", data={
        "excited": "I want to get my small cleaning company organized",
        "blocker": "People call and I sometimes forget to follow up.",
        "business_name": "Subscription Test Company",
        "contact_name": "Owner Person", "email": email,
        "industry": "cleaning", "consent": "yes"})
    assert result.status_code == 303
    token = result.headers["location"].rsplit("/", 1)[-1]
    checkout = client.post(f"/assessment/r/{token}/order",
                           data={"offer_code": "operate", "approved_name": "Owner Person",
                                 "agree": "yes"})
    assert checkout.status_code == 303, checkout.text[:250]
    assert checkout.headers["location"].startswith("https://checkout.stripe.com/")
    with session_scope() as db:
        row = db.scalar(select(ServiceOrder).where(ServiceOrder.offer_code == "operate")
                        .order_by(ServiceOrder.created_at.desc()))
        return client, row.id, row.provider_ref


def test_checkout_price_ids_and_product_scoped_promotion(stripe):
    from app.services.payments import PaymentError

    provider, calls = stripe
    offer = {"name": "Growth Partner", "billing": "monthly", "price_cents": 9900,
             "stripe_price_id": PRICE, "allow_promotion_codes": True}
    provider.create_checkout(
        order_id=str(uuid.uuid4()), offer=offer,
        customer_email="owner@example.test",
        success_url="https://example.test/success",
        cancel_url="https://example.test/cancel")
    body = parse_qs(calls[0].content.decode())
    assert body["allow_promotion_codes"] == ["true"]
    assert body["line_items[0][price]"] == [PRICE]
    assert "line_items[0][price_data][unit_amount]" not in body
    assert body["mode"] == ["subscription"]
    assert body["subscription_data[metadata][order_id]"]
    assert provider.secret_key not in calls[0].content.decode()
    with pytest.raises(PaymentError):
        provider.create_checkout(
            order_id="test", offer={**offer, "stripe_price_id": ""},
            customer_email="x@example.test",
            success_url="https://example.test/ok", cancel_url="https://example.test/no")
    assert len(calls) == 1


def test_recurring_out_of_order_discount_grace_and_cancellation(platform, stripe):
    from app.db import session_scope
    from app.models import BillingSubscription, Organization, ServiceOrder

    client, order_id, session = recurring_order(platform)
    now_ts = int(time.time())
    sid = "sub_test_" + uuid.uuid4().hex
    cust = "cus_test_" + uuid.uuid4().hex
    invoice_id = "in_test_" + uuid.uuid4().hex
    sub = {"id": sid, "customer": cust, "status": "active",
           "metadata": {"order_id": str(order_id)},
           "current_period_end": now_ts + 86400 * 30}
    # Provider webhooks can precede checkout.session.completed.
    first = signed(client, event("customer.subscription.created", sub, now_ts + 5))
    assert first.json()["outcome"] == "subscription_updated"
    inv = {"id": invoice_id, "customer": cust, "subscription": sid}
    assert signed(client, event("invoice.paid", inv, now_ts + 6)).json()["outcome"] == "invoice_paid"
    with session_scope() as db:
        row = db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == sid))
        assert row.order_id == order_id and not row.entitlement_active and row.org_id is None

    zero_due = {"id": session, "customer": cust, "subscription": sid,
                "mode": "subscription", "currency": "usd", "payment_status": "no_payment_required",
                "amount_subtotal": 9900, "amount_total": 0,
                "total_details": {"amount_discount": 9900, "amount_tax": 0}}
    bad = dict(zero_due, amount_subtotal=9800)
    assert signed(client, event("checkout.session.completed", bad, now_ts + 7)).json()[
        "outcome"] == "amount_mismatch"
    with session_scope() as db:
        assert db.get(ServiceOrder, order_id).org_id is None

    good = event("checkout.session.completed", zero_due, now_ts + 8)
    response = signed(client, good)
    assert response.json()["outcome"] == "paid", response.text
    assert signed(client, good).json()["outcome"] == "duplicate"
    with session_scope() as db:
        order = db.get(ServiceOrder, order_id)
        assert order.status == "provisioned"
        assert order.paid_at is not None
        assert (order.settled_amount_cents, order.discount_amount_cents, order.tax_amount_cents) == (
            0, 9900, 0)
        org_id = order.org_id
        subrow = db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == sid))
        assert subrow.entitlement_active and subrow.org_id == org_id
    with session_scope(org_id=org_id) as db:
        org = db.get(Organization, org_id)
        assert org.settings["billing"]["amount_cents"] == 0
        assert org.settings["billing"]["discount_cents"] == 9900
        assert org.settings["subscriptions"]["operate"]["access_active"] is True

    # An older failure cannot invalidate a newer paid invoice.
    assert signed(client, event("invoice.payment_failed", inv, now_ts + 4)).json()[
        "outcome"] == "stale_invoice_event"
    with session_scope() as db:
        assert db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == sid)).invoice_status == "paid"

    # A new failure grants a bounded grace period instead of endless paid access.
    assert signed(client, event("invoice.payment_failed", inv, now_ts + 11)).json()[
        "outcome"] == "invoice_failed"
    with session_scope() as db:
        from datetime import timedelta
        from app.services.orders import active_subscription_for
        record = db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == sid))
        assert record.grace_until is not None and record.entitlement_active
        assert active_subscription_for(db, org_id, "operate")
        assert not active_subscription_for(db, org_id, "operate",
                                           at=record.grace_until + timedelta(seconds=1))

    # A successful renewal can restore current access and extend the billing period.
    renewed = dict(sub, current_period_end=now_ts + 86400 * 60)
    assert signed(client, event("invoice.paid", inv, now_ts + 12)).json()[
        "outcome"] == "invoice_paid"
    assert signed(client, event("customer.subscription.updated", renewed, now_ts + 13)).json()[
        "outcome"] == "subscription_updated"
    with session_scope() as db:
        record = db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == sid))
        assert record.entitlement_active and record.invoice_status == "paid"
        assert record.period_end.timestamp() == now_ts + 86400 * 60

    # Cancellation revokes access. Later delivery of an older invoice cannot undo it.
    cancelled = dict(sub, status="canceled")
    assert signed(client, event("customer.subscription.deleted", cancelled, now_ts + 15)).json()[
        "outcome"] == "subscription_updated"
    assert signed(client, event("invoice.paid", inv, now_ts + 12)).json()[
        "outcome"] == "invoice_paid"
    with session_scope() as db:
        record = db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == sid))
        assert record.status == "canceled" and not record.entitlement_active
    with session_scope(org_id=org_id) as db:
        org = db.get(Organization, org_id)
        assert org.settings["subscriptions"]["operate"]["access_active"] is False


def test_unmatched_stripe_event_cannot_create_an_org(platform, stripe):
    from app.db import session_scope
    from app.models import BillingSubscription, Organization
    client = client_for(platform)
    sid = "sub_test_" + uuid.uuid4().hex
    timestamp = int(time.time())
    assert signed(client, event("invoice.paid", {"subscription": sid,
                     "id": "in_unknown"}, timestamp)).json()["outcome"] == "invoice_paid"
    with session_scope() as db:
        item = db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == sid))
        assert item.order_id is None and item.org_id is None and not item.entitlement_active
