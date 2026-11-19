# MF-091 — Existing founder-beta owner -> native Stripe Checkout

Status: local feature pending independent re-review. NOT deployed.

This is the same MeasuredField application, not a beta copy. A signed-in
owner uses Settings to open the existing $99/month operate Stripe Price
in Stripe Checkout, where promotion codes can reduce the amount due to $0.
The verified subscription is tied to the already-selected workspace.

## Invariants and verified behavior
- Authenticated OWNER membership and CSRF are required; the tenant comes from
  the server session, not submitted form fields or Stripe customer email.
- Only a positive integer approved offer price and registered recurring Price
  permit Checkout creation. Exact order-id metadata binds the Stripe session.
- Migration 0013 permits a nullable assessment_id ONLY to represent purchases
  started inside an existing owner's workspace. Public assessments unchanged.
- Organization row lock serializes checkout starts. In-progress orders block
  a duplicate; a pending session is resumed only after authenticated GET to
  Stripe's fixed session endpoint verifies the same session id, order
  reference, subscription mode, and open status.
- Only signed Stripe checkout.session.expired events or a verified provider
  API response can cancel an expired session. No time-only 24-hour shortcut.
- Signed checkout.session.completed enforces stored pre-discount subtotal,
  zero final amount for no_payment_required, and discount/tax arithmetic.
  Subscription and invoice webhooks establish current period and entitlement.
  A browser success URL or coupon by itself grants no authorization.
- Tenant-independent signed expiration is captured by the unique global
  PaymentEvent ledger; the webhook never bypasses tenant RLS to write an
  organization-specific audit row.
- UI separates founding beta, pending/resumable checkout, active subscription,
  and linked Stripe billing management. Existing organization is preserved.
- All development testing uses disposable restricted PostgreSQL, synthetic
  signed Stripe webhook events and a mocked payment API; no live payment.

## Release gates
1. Independent review of FULL signed-webhook handler and lifecycle code, not
   only the MF-091 diff. Original review of 3c6874f requested changes.
2. Confirm Stripe webhook endpoint subscribes to checkout.session.expired along
   with existing payment/subscription events; verify signing secret safely.
3. Apply database migration 0013 and confirm reliable production backup and
   recovery before live activation (free PostgreSQL limitations documented).
4. Check responsive/mobile owner settings and one real voluntary founder
   checkout at exactly $0, without test charging other users.
5. ChatGPT OAuth, connected MCP tool actions, and write authorization are
   separate workstreams; Checkout does not make those suddenly connected.
6. Limited redemption private promotion code is not unrestricted public beta.
