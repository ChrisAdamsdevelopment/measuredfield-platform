"""MF-092: public new-customer checkout fails closed against bad Stripe host."""
import uuid

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import ServiceOrder
from .conftest import client_for


@pytest.mark.parametrize("valid", [False, True])
def test_public_assessment_checkout_redirect_host_validated(platform, monkeypatch, valid):
    client = client_for(platform)
    email = "synthetic-payment-" + uuid.uuid4().hex[:11] + "@fictional.test"
    response = client.post("/assessment", data={
        "excited": "Our fictional cleaning customers appreciate reliable scheduling and prompt service.",
        "blocker": "We need a clear website, more inquiries and better organized quote follow-up.",
        "business_name": "Synthetic Customer " + uuid.uuid4().hex[:7],
        "contact_name": "Synthetic Buyer", "email": email,
        "website": "", "industry": "cleaning", "consent": "yes",
    })
    assert response.status_code == 303
    token = response.headers["location"].rsplit("/", 1)[1]
    assert client.get("/assessment/r/" + token).status_code == 200
    sessions = []

    class FakeStripe:
        def create_checkout(self, **kwargs):
            sessions.append(kwargs)
            sid = "cs_test_synthetic_valid12345"
            return {"id": sid,
                    "url": ("https://checkout.stripe.com/c/pay/" + sid if valid
                            else "https://checkout.stripe.com.evil/c/pay/" + sid)}

    with monkeypatch.context() as m:
        m.setattr("app.routers.public.payments.get_provider", lambda settings=None: FakeStripe())
        purchase = client.post("/assessment/r/" + token + "/order", data={
            "offer_code": "launch", "approved_name": "Synthetic Buyer", "agree": "yes",
        })
    assert purchase.status_code == 303
    assert len(sessions) == 1
    assert sessions[0]["offer"]["price_cents"] == 19900
    with session_scope() as db:
        orders = db.scalars(select(ServiceOrder).where(
            ServiceOrder.offer_code == "launch").order_by(ServiceOrder.created_at.desc())).all()
        assert orders
        order = next(x for x in orders if x.approved_name == "Synthetic Buyer"
                     and str(x.id) in (sessions[0]["success_url"]))
        if valid:
            assert purchase.headers["location"] == (
                "https://checkout.stripe.com/c/pay/cs_test_synthetic_valid12345")
            assert order.status == "payment_pending"
            assert order.provider_ref == "cs_test_synthetic_valid12345"
            assert purchase.headers["referrer-policy"] == "no-referrer"
        else:
            assert purchase.headers["location"].startswith("/order/")
            assert order.status == "approved_unpaid"
            assert order.provider_ref is None and order.payment_provider == "none"
