# MF-006 — Stripe promotions, subscriptions and existing-customer Growth Partner

**Implementation status:** Local application integration and synthetic PostgreSQL regressions; NOT enabled against a real Stripe account. No live Price IDs, products, promotion codes, customer payments or API secrets were created or committed.

## Operational principle
A Stripe Checkout redirect is NOT proof of payment or of subscription access. Only a signed, idempotent Stripe event can move the order into the paid/provisioned state. The backend stores source Stripe subscription/invoice facts separate from the owner-visible display snapshot, which can become stale. Paid-feature access checks should call `orders.active_subscription_for` after authenticating the owner and business.

## Checkout configuration
`app/services/payments.py` supports either the existing inline `price_data` (legacy offers) or a real `stripe_price_id` in a configured offer. A recurring offer also sends `subscription_data[metadata][order_id]` so early subscription events can be correlated with the approved order. Promotional fields in an offer are configuration:
```json
{
  "code": "growth_partner",
  "name": "MeasuredField Growth Partner",
  "price_cents": 9900,
  "billing": "monthly",
  "add_on": true,
  "summary": "Owner-approved founder support scope.",
  "includes": ["Approved limited service description"],
  "stripe_price_id": "price_FROM_A_REAL_STRIPE_TEST_OR_LIVE_CATALOG",
  "allow_promotion_codes": true,
  "promotion_code": "GUIDEDLAUNCH",
  "promotion_disclosure": "First month of Growth Partner free; then $99 per month unless cancelled. Your Business Workspace is billed separately."
}
```
This is a **schema example**, not a real Price ID. Do not insert the placeholder into production `MF_OFFERS_JSON`. If an offer advertises a promotion but has no valid configured Stripe Price ID, Checkout refuses creation rather than accepting a code it cannot apply.

**Existing customers only:** `growth_partner` is deliberately excluded from public assessment pricing, and `create_order` rejects `add_on` offers. `GET /app/billing` and POST `/app/billing/growth-partner` require a signed-in owner and CSRF proof, a real provisioned Business Workspace, and a checked service-terms acknowledgement. The add-on creates an order bound to the EXISTING tenant, not a new tenant or owner invitation. The feature is automatically hidden with `MF_PAYMENT_PROVIDER=none` or an incomplete/unapproved price.

**One-item limitation:** First version creates a separate Stripe subscription for Growth Partner. It does not combine Core and Growth Partner in one checkout and it does not automatically verify that the customer has a paid recurring Core plan. Its core precondition is an already provisioned workspace; product packaging is still an owner decision.

## Promotion mechanics
Create a Stripe **Product** for Growth Partner, a **recurring USD $99 monthly Price** and a **coupon of 100% off for duration=once, applies_to=Growth Partner product**; create its customer-facing Promotion Code `GUIDEDLAUNCH` with confirmed max redemptions and expiry. Product image can be attached later. No coupons exist in connected live merchant account yet.
The frontend exposes Copy Code only when all of `payments_live`, real `stripe_price_id`, `allow_promotion_codes`, `promotion_code` and `promotion_disclosure` are configured. The copy script is a static same-origin asset and does not report usage externally.
Stripe Checkout uses `allow_promotion_codes=true`, so customer applies the code within Stripe; application does NOT assume entitlement based on copied code or redirect.

## Signed webhook processing
`app/services/orders.py`: `checkout.session.completed` verifies Checkout pre-discount `amount_subtotal` matches the owner-approved order list price and that `amount_total = subtotal - amount_discount + amount_tax`. Signed 100%-off invoices with `payment_status=no_payment_required` can therefore provision at $0. Unsupported/ambiguous breakdowns fail closed; old synthetic inline test sessions without subtotals retain strict exact-total matching.
Recurring events:
- `customer.subscription.created/updated/deleted` update subscription status, cancel flag and period end (top-level or item-level Stripe period end); a canceled subscription is terminal for that ID.
- `invoice.paid` and `invoice.payment_failed` use independent ordering checkpoints, plus a bounded 72-hour grace policy for recent failures.
- `charge.refunded` applies to a subscription ONLY if a verified event contains an invoice reference matching an observed invoice ID; unknown refund mapping is ignored, not guessed.
- Duplicate event IDs are recorded in `payment_events` and treated idempotently; stale events cannot overwrite newer subscription/invoice state.
- `BillingSubscription` is server-only, not a tenant-exposed RLS table; tenant state is only made visible after membership checks. No provider customer secrets are included in owner-facing UI.
- `active_subscription_for` evaluates access *at query time* to avoid indefinitely trusting a stale paid boolean after expiry.
- Customer-facing billing history `/app/billing` lists verified internal orders/periods, no credit-card details.

## Known unfinished hard release gates
1. **Price and tax approval.** Live Stripe merchant currently has no products, prices or tax registrations; Stripe Tax status is pending with missing head-office data. Owner must approve core subscription price, any setup fee, Growth Partner scope/cap and first-month duration/redemption count.
2. **Sandbox integration.** Connect Stripe TEST-mode context; create real synthetic Prices/coupon; verify one-time + monthly Checkout, subscription renewals, failure recovery, cancellation, refund, tax calculation and copy-code redemption. No test-mode API is currently connected to this session.
3. **Actual Stripe API event shape and fetching.** The event handler is tested with synthetic signed payloads. Validate production API version and invoice subscription field variants using sandbox. Customer Portal must be implemented and verified for cancellation, card change and refund disclosures; no live subscription should be enabled until then.
4. **Multiple subscriptions per organization.** Product entitlements and workspace access are not yet integrated into every paid-feature route. Decide whether a canceled Core plan suspends all Growth Partner operations, and implement robust entitlement gating.
5. **Security and event ordering.** Billing mutation must be serialized under transaction locks for concurrent deliveries. `stripe_customer_id` to membership ownership must be checked before account/portal sessions, never trusted from a URL.
6. **Trial / introductory handling.** One-month coupon is a discount on a recurring invoice, not a non-renewing free plan. Make next charge explicit and ensure cancellation remains available. Zero-charge checkout must still have valid subscription and verified Checkout session binding.
7. **Customer support/consumer terms.** Refund, cancellation, accessibility and response hours must be documented. No automatic marketing email or billing statements that are not actually supported.

## Test procedure (all synthetic)
`python -B scripts/run_disposable_pg_audit.py --full-suite` runs isolated Postgres with separate schema owner and runtime, applies Alembic migration 0005, validates RLS and runs:
- `tests/test_subscription_reconciliation.py`: price-only checkout, valid 100%-discount amount, duplicate, out-of-order renewal/cancellation/failed invoice, grace expiration, unrelated subscription ignored;
- `tests/test_promo_copy_ui.py`: code hidden by default and when pricing/disclosure incomplete;
- `tests/test_growth_partner_addon.py`: existing-customer owner/CSRF/consent, separate extra checkout and never duplicate tenant.
After fixes, run `git diff --check`, local CI secret scanning and independent reviewer before staging; this document is not deployment authorization.

Official references: https://docs.stripe.com/billing/subscriptions/webhooks | https://docs.stripe.com/billing/subscriptions/coupons | https://docs.stripe.com/customer-management/integrate-customer-portal
