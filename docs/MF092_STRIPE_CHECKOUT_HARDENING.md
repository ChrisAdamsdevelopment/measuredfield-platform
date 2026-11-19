# MF-092 — Strict Stripe-hosted Checkout validation

Status: local source changes; no real Stripe payment, price, coupon or production website changes.

## Why this exists
The previous public-assessment checkout path accepted URL/id fields from a Stripe
adapter without checking their destination. Also, the default httpx client
honored ambient HTTPS_PROXY configuration. The founder route was hardened in
MF-091, but all paid paths need defense-in-depth at the payment adapter and route.

## Changes
- Shared payments.hosted_checkout_session_valid() accepts only a string
  cs_ Checkout Session ID, HTTPS origin exactly checkout.stripe.com (no port or
  userinfo), and path /c/pay/ followed by the SAME session id. No URL query
  parameters. Stripe-supplied fragment is allowed because hosted Stripe
  Checkout may carry a fragment.
- StripeProvider.create_checkout rejects malformed/non-JSON/invalid-session
  responses before any caller can redirect. It always uses a stable server
  idempotency key containing the internally generated order ID.
- StripeProvider creates its normal httpx.Client with trust_env=False and
  bounded timeout. A test can still inject an explicit httpx.MockTransport.
- Public assessment checkout independently applies session verification to
  the pluggable provider response. Invalid provider data keeps an approved
  but unpaid order; only a valid Stripe Checkout can set payment_pending.
- A valid redirect has no-referrer and no-store headers, reducing accidental
  leak of one-time checkout or assessment links.
- Existing payment/subscription mock fixtures were updated to realistic
  Stripe-hosted session URLs; the validation was not weakened for tests.

## Test and security scope
All automated tests use synthetic offers, businesses and fake Stripe responses.
No calls to production Stripe, no charges, coupon redemptions or email sends.
Source review and full disposable restricted-PostgreSQL suite required before
publishing. Payment Link direct purchase claiming remains separate MF-087.
MF-091's production checkout.session.expired delivery and the live database
backup/recovery constraints are separate external release gates.

## Remaining risk
This URL verification ensures the returned session is on Stripe's official
checkout host and matches the given session id. It cannot verify the Session
belongs to the intended price/customer until signed provider events and exact
order/price metadata reconcile on the backend. Never grant access from a
browser redirect or unsigned client-provided Checkout Session response.
