"""MF-092: strict Stripe Checkout response parser and direct TLS transport."""
from __future__ import annotations

import httpx
import pytest

from app.services import payments
from app.services.payments import PaymentError, StripeProvider, hosted_checkout_session_valid

GOOD_ID = "cs_test_synthetic_valid12345"
GOOD_URL = "https://checkout.stripe.com/c/pay/" + GOOD_ID


@pytest.mark.parametrize("session", [
    None, "", 3, [], {}, {"id": GOOD_ID}, {"url": GOOD_URL},
    {"id": "bad_id", "url": GOOD_URL},
    {"id": GOOD_ID, "url": "https://checkout.stripe.com.evil/c/pay/" + GOOD_ID},
    {"id": GOOD_ID, "url": "http://checkout.stripe.com/c/pay/" + GOOD_ID},
    {"id": GOOD_ID, "url": "https://checkout.stripe.com:444/c/pay/" + GOOD_ID},
    {"id": GOOD_ID, "url": "https://user:pass@checkout.stripe.com/c/pay/" + GOOD_ID},
    {"id": GOOD_ID, "url": "https://checkout.stripe.com.evil/?target=" + GOOD_URL},
    {"id": GOOD_ID, "url": "https://checkout.stripe.com/other/" + GOOD_ID},
    {"id": GOOD_ID, "url": "https://checkout.stripe.com/c/pay/cs_test_different_session"},
    {"id": GOOD_ID, "url": GOOD_URL + "?redirect=https://evil.test"},
    {"id": GOOD_ID, "url": GOOD_URL + "\n"},
    {"id": GOOD_ID, "url": "https://checkout.stripe.com\\@evil.test/c/pay/" + GOOD_ID},
])
def test_invalid_hosted_session_is_denied(session):
    assert hosted_checkout_session_valid(session) is False


def test_valid_session_accepts_stripe_provided_fragment():
    assert hosted_checkout_session_valid({"id": GOOD_ID, "url": GOOD_URL})
    assert hosted_checkout_session_valid({"id": GOOD_ID, "url": GOOD_URL + "#fid=test"})


def test_default_stripe_transport_disables_ambient_proxies(monkeypatch):
    original = httpx.Client
    arguments = []

    def spy(*args, **kwargs):
        arguments.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setenv("HTTPS_PROXY", "http://example.invalid:1234")
    monkeypatch.setattr(payments.httpx, "Client", spy)
    provider = payments.StripeProvider("sk_test_synthetic", "whsec_synthetic")
    try:
        assert len(arguments) == 1
        assert arguments[0]["trust_env"] is False
        assert arguments[0]["timeout"] == 15
    finally:
        provider.client.close()


@pytest.mark.parametrize("body", [
    {"id": GOOD_ID, "url": "https://checkout.stripe.com.evil/c/pay/" + GOOD_ID},
    {"id": "malformed", "url": GOOD_URL}, {"id": GOOD_ID, "url": "/relative"},
    {"id": GOOD_ID, "url": None},
    [],
])
def test_adapter_refuses_invalid_provider_session(body):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body)))
    provider = StripeProvider("sk_test_synthetic", "whsec_synthetic", client=client)
    with pytest.raises(PaymentError):
        provider.create_checkout(
            order_id="74c3fb56-e0d2-41e0-8307-675ec292f831",
            offer={"name":"Synthetic","billing":"one_time","price_cents":19900},
            customer_email="test@fictional.test",success_url="https://spectracleanse.com/order/ok",
            cancel_url="https://spectracleanse.com/order/cancel")
    client.close()


def test_adapter_rejects_non_json_provider_response():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"not valid JSON")))
    provider = StripeProvider("sk_test_synthetic", "whsec_synthetic", client=client)
    with pytest.raises(PaymentError):
        provider.create_checkout(
            order_id="74c3fb56-e0d2-41e0-8307-675ec292f831",
            offer={"name":"Synthetic","billing":"one_time","price_cents":19900},
            customer_email="test@fictional.test",success_url="https://spectracleanse.com/order/ok",
            cancel_url="https://spectracleanse.com/order/cancel")
    client.close()
