# MF-087 — Direct Stripe Payment Link purchase fulfillment

STATUS: LOCAL IMPLEMENTATION AND 1,148 FULL PG TESTS PASSED; NOT DEPLOYED. Independent review pending.

## Purchase evidence
Two existing exact allowlisted Stripe links:
- Business Workspace: plink_1UcxSA2LWll6Mkhu7MR1P3dr, price_1Ub6rZ2LWll6MkhusWBI1ebk, operate, $99 monthly.
- Growth Partner: plink_1UcxUD2LWll6MkhuecqI4wT9, price_1Ub6rh2LWll6MkhuYiaLXu2m, growth_partner, $99 monthly. Requires active operate.

Only a signed Stripe checkout.session.completed webhook for an unmatched order and allowlisted link reaches this path. Server verifies subscription mode, exact USD $99 pre-discount amount, correct net amount and discount arithmetic, paid/no_payment_required state, customer/email/subscription IDs, actual Stripe GET line items with correct price and quantity one, active subscription and future billing period via a separate authenticated GET. API failures yield retryable HTTP 503; the signed PaymentEvent is rolled back. Unverified or ineligible purchases cannot unlock anything.

## Buyer experience
A valid direct Checkout records a platform PendingPaymentLinkClaim with unique checkout session and Stripe subscription IDs, but creates NO account or business and grants NO entitlement. If email is configured, a plain information email without login tokens points to the claim process.

Buyer can use /purchase/claim for instructions and /signup to establish a verified account. Only a signed-in OWNER with a redeemed email-issued invite/reset proof matching the exact billing email sees pending claims in /app/billing/claim. CSRF POST chooses the current server-derived business (never a submitted tenant ID), locks the purchase, freshly verifies active Stripe subscription, rejects duplicate/canceled/refunded/unlinked customer state, and binds one ServiceOrder and BillingSubscription to that existing organization. Both $0 and positive-dollar purchases use the same verified path. Cancellation restores previous founding beta plan; Stripe portal available for a linked customer.

## Tests and source
- Migration 0014 after owner checkout migration 0013, platform-only payment_link_claims table with uniqueness and status checks.
- Six focused real disposable PostgreSQL tests passed: signed zero-dollar claim, wrong email/tenant, forged CSRF, duplicate signed event, price/totals, inactive subscription, Growth Partner precondition, and transient API retry.
- Full regression 1,148 passed, zero errors/skips/failures, CI_GATE_PASS. No live purchases or customer writes.
- Independent security review, Ruff and tracked/staged secret scan are required prior to approval and commit.
- Existing Payment Links remain unchanged.

## Production gates (NOT satisfied)
1. Verified recoverable production Postgres snapshot and safe migrations 0013 then 0014.
2. New signed checkout.session.expired webhook event only after revised handler deployment; live endpoint we_1UcyaC2LWll6Mkhu0almJSv3 currently has seven older event types.
3. Test-mode genuine webhook delivery, no-charge founder Checkout, Stripe portal, cancel/retry, and final verification of real customer_details/line_items/subscription shapes.
4. Unmatched payments require operator review; verified alias recovery and exception support flow remain planned; never guess tenant or grant access from email alone.
5. ChatGPT OAuth client binding and write-capable scoped tools remain separate from billing fulfillment.

This is a development QA milestone only; no Stripe charges, live emails, production database migration or Render deployment occurred.

## Independent review corrections (local, November 18)
Claude requested changes to the first implementation. Addressed:
- The persisted subtotal is now the exact validated subtotal from signed Stripe event, not reconstituted catalog price.
- Migration 0014 now enforces total=subtotal-discount+tax at database level.
- An immutable platform-only PaymentLinkClaimReview event records the operator, reason, decision, and affected claim. A separate, operator-authenticated and CSRF-protected queue at /operator/payment-link-claims lists expired/needs_review claims.
- The operator recheck requires live authenticated Stripe subscription verification; it reopens ONLY an unassigned claim with no existing signed terminal cancellation/refund or linked order. Reopening grants no user, tenant, or entitlement. Buyer must still complete email-verified owner claim. Terminally canceled cases remain needs_review for support handling.
- A pending or active purchase may not be reassigned by the operator directly; no email-as-authorization shortcut exists.
- Templates no longer mislabel unknown offer codes; strict has_more=False requirement documented.
- Email-account proof is deliberately global for unique User.email; prior redeemed email-delivered auth tokens remain evidence of mailbox control even if original org membership later changes. Current owner membership in selected business remains mandatory.
