"""MF-091: real Stripe adapter form shape with synthetic HTTP transport."""
import httpx

from app.services.payments import StripeProvider


def test_registered_price_monthly_checkout_offers_real_promo_input():
    posted=[]
    def handle(request):
        assert request.url.path == "/v1/checkout/sessions"
        posted.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200,json={
            "id":"cs_test_synthetic_valid",
            "url":"https://checkout.stripe.com/c/pay/cs_test_synthetic_valid"})
    client=httpx.Client(transport=httpx.MockTransport(handle))
    provider=StripeProvider("sk_test_synthetic","whsec_test",client=client)
    response=provider.create_checkout(
        order_id="8ad100f9-0aca-4c5d-8cd0-a501f0cb8325",
        offer={"name":"Ongoing workspace","price_cents":9900,"billing":"monthly",
               "stripe_price_id":"price_synthetic1234","allow_promotion_codes":True},
        customer_email="owner@fictional.test",
        success_url="https://spectracleanse.com/app/settings",
        cancel_url="https://spectracleanse.com/app/settings")
    assert response["id"]=="cs_test_synthetic_valid"
    assert len(posted)==1
    fields=posted[0]
    assert fields["mode"]=="subscription"
    assert fields["line_items[0][price]"]=="price_synthetic1234"
    assert fields["line_items[0][quantity]"]=="1"
    assert fields["allow_promotion_codes"]=="true"
    assert fields["subscription_data[metadata][order_id]"]=="8ad100f9-0aca-4c5d-8cd0-a501f0cb8325"
    assert fields["metadata[order_id]"]=="8ad100f9-0aca-4c5d-8cd0-a501f0cb8325"


def test_checkout_recovery_requires_exact_stripe_session_and_server_order():
    """Provider GET may resume, expire or await signed webhook; no email-based claim."""
    from types import SimpleNamespace
    from uuid import UUID
    from app.routers.owner_checkout import _inspect_stripe_checkout

    order = SimpleNamespace(
        id=UUID("8ad100f9-0aca-4c5d-8cd0-a501f0cb8325"),
        provider_ref="cs_test_synthetic_valid",
    )
    state = {"status": "open", "payment_status": "unpaid",
             "url": "https://checkout.stripe.com/c/pay/cs_test_synthetic_valid"}
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={
            "id": order.provider_ref,
            "client_reference_id": str(order.id),
            "mode": "subscription",
            **state,
        })
    provider = StripeProvider(
        "sk_test_synthetic", "whsec_synthetic",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert _inspect_stripe_checkout(provider, order) == {
        "status": "open", "url": state["url"]}
    assert calls == ["/v1/checkout/sessions/cs_test_synthetic_valid"]
    state["payment_status"] = "no_payment_required"
    assert _inspect_stripe_checkout(provider, order) is None  # not a valid OPEN state
    state.update({"status": "expired", "payment_status": "unpaid"})
    assert _inspect_stripe_checkout(provider, order) == {"status": "expired"}
    state["status"] = "complete"
    assert _inspect_stripe_checkout(provider, order) == {"status": "complete"}
    state.update({"status": "open", "payment_status": "unpaid",
                  "url": "https://checkout.stripe.com.evil/c/pay/forged"})
    assert _inspect_stripe_checkout(provider, order) is None

    original_id = order.id
    order.id = UUID("12345678-1234-1234-1234-123456789012")
    assert _inspect_stripe_checkout(provider, order) is None
    order.id = original_id
    order.provider_ref = "https://attacker.example/billing"
    assert _inspect_stripe_checkout(provider, order) is None


def test_checkout_recovery_is_closed_on_stripe_api_errors():
    from types import SimpleNamespace
    from uuid import UUID
    from app.routers.owner_checkout import _inspect_stripe_checkout
    order = SimpleNamespace(
        id=UUID("8ad100f9-0aca-4c5d-8cd0-a501f0cb8325"),
        provider_ref="cs_test_synthetic_valid",
    )
    provider = StripeProvider(
        "sk_test_synthetic", "whsec_synthetic",
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"error":"maintenance"}))),
    )
    assert _inspect_stripe_checkout(provider, order) is None
