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
