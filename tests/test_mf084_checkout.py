"""MF-084 adversarial amount and checkout data safety (no provider network)."""
from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest


def test_approved_price_and_discount_math_fail_closed():
    from app.services.orders import _checkout_total

    good = {
        "amount_subtotal": 9900,
        "amount_total": 0,
        "total_details": {"amount_discount": 9900, "amount_tax": 0},
    }
    assert _checkout_total(good, 9900) == (0, 9900, 0)
    assert _checkout_total({**good, "amount_total": 1}, 9900) is None
    assert _checkout_total({**good, "amount_subtotal": 9899}, 9900) is None
    assert _checkout_total({**good, "total_details": {"amount_discount": 10000}}, 9900) is None
    assert _checkout_total({**good, "total_details": {"amount_discount": -1}}, 9900) is None
    assert _checkout_total({**good, "total_details": {"amount_tax": -5}}, 9900) is None
    assert _checkout_total({**good, "total_details": {"amount_discount": "NaN"}}, 9900) is None
    assert _checkout_total({"amount_total": 9900}, 9900) == (9900, 0, 0)
    assert _checkout_total({"amount_total": 0}, 9900) is None


def test_recurring_checkout_has_real_price_and_order_correlation():
    from app.services.payments import StripeProvider

    observed = []

    def transport(req):
        observed.append(parse_qs(req.content.decode()))
        return httpx.Response(200, json={
            "id": "cs_test_price_validation", "url": "https://checkout.stripe.com/c/pay/cs_test_price_validation",
        })

    provider = StripeProvider(
        "sk_test_dummy_offline", "whsec_dummy_offline",
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    response = provider.create_checkout(
        order_id="synthetic-allowed-order-id",
        offer={"price_cents": 9900, "name": "MeasuredField Workspace",
               "billing": "monthly", "stripe_price_id": "price_Valid123",
               "allow_promotion_codes": True},
        customer_email="test@example.invalid",
        success_url="https://example.invalid/complete",
        cancel_url="https://example.invalid/cancel",
    )
    assert response["id"] == "cs_test_price_validation"
    form = observed[0]
    assert form["mode"] == ["subscription"]
    assert form["line_items[0][price]"] == ["price_Valid123"]
    assert form["allow_promotion_codes"] == ["true"]
    assert form["subscription_data[metadata][order_id]"] == ["synthetic-allowed-order-id"]
    assert "line_items[0][price_data][unit_amount]" not in form


@pytest.mark.parametrize("value", ["", "not_a_price", "price_bad/path", "price_", "price_fake?data"])
def test_invalid_stripe_price_ids_do_not_make_provider_request(value):
    from app.services.payments import PaymentError, StripeProvider

    calls = []

    def no_network(req):
        calls.append(req)
        raise AssertionError("Never contact Stripe for invalid price IDs")

    provider = StripeProvider(
        "sk_test_dummy_offline", "whsec_dummy_offline",
        client=httpx.Client(transport=httpx.MockTransport(no_network)),
    )
    with pytest.raises(PaymentError):
        provider.create_checkout(
            order_id="synthetic-only",
            offer={"price_cents": 9900, "name": "Test", "billing": "monthly",
                   "allow_promotion_codes": True, "stripe_price_id": value},
            customer_email="test@example.invalid",
            success_url="https://example.invalid/complete",
            cancel_url="https://example.invalid/cancel",
        )
    assert calls == []


def test_invoice_association_uses_valid_subscription_id_only():
    from app.services.orders import _invoice_subscription_id

    assert _invoice_subscription_id({
        "parent": {"subscription_details": {"subscription": "sub_expected123"}},
    }) == "sub_expected123"
    assert _invoice_subscription_id({
        "subscription": "sub_top123",
        "parent": {"subscription_details": {"subscription": "sub_nested123"}},
    }) == "sub_top123"
    assert _invoice_subscription_id({
        "parent": {"subscription_details": {"subscription": "customer_other123"}},
    }) is None
    assert _invoice_subscription_id({}) is None
