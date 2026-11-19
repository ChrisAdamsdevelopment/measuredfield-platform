# MF-086: Stripe Customer Billing Portal (owner-only)

**Purpose.** A real paying owner with a verified Stripe subscription can manage invoices, payment details and cancellation directly in Stripe, without a MeasuredField operator or emailing sensitive card details.

**Authentication and authorization.** New POST `/app/billing/portal` requires existing authenticated tenant `member_post('billing.read')` session + CSRF, then explicit `role == owner`. The Stripe Customer ID comes ONLY from a `BillingSubscription` linked by `ServiceOrder` to the exact active org with `payment_provider=stripe`, `paid_at` and signed event evidence. It rejects multi-customer conflicts rather than guessing. No field for customer ID/email is accepted from the browser.

**Stripe provider.** POST `https://api.stripe.com/v1/billing_portal/sessions` with the server secret and exact `customer` + `return_url=https://spectracleanse.com/app/settings`. Reject non-200, timeout and redirects outside strict `https://billing.stripe.com/p/session/` in response. App never records or exposes the Stripe Customer ID or URL in its own HTML. Stripe handles card entry, invoices, cancellations and subsequent events; our existing signature-checked webhook reconciles changes.

**Workspace lifecycle.** A tenant in `deletion_pending` status may still open Stripe billing while the deletion grace period runs, so its owner can cancel a subscription before deletion. Fully deleted tenants cannot authenticate or open the portal.

**Beta accounts.** `founding_beta` and unpaid consultation accounts get no portal access and no card requirement. Staff, admin, read-only and visitors cannot launch a payment session. A paid owner with no verified Stripe linkage gets a clear error, not a guessed customer.

**Verified live Stripe portal configuration — November 17, 2026.** Created and read back `bpc_1UczyC2LWll6Mkhul06ZHxOp` in the correct live Stripe account, marked active and default. Invoice history and payment-method updates are enabled, subscription cancellation at period end is enabled, self-serve anonymous portal login is disabled, price-switching is disabled. This changes no subscriptions and takes no charge.

**Outstanding for production.** Direct standalone Payment Links currently lack `ServiceOrder` correlation, so users purchasing through those links do **not** automatically get this portal. Do not claim complete fulfillment from link purchase. Confirm browser flow with a genuine connected subscription after customer-initiated payment; no QA charge was made.

**Release gate.** Real restricted PostgreSQL fixture, adversarial cross-tenant & CSRF tests, HTTP mock provider redirects/errors, full suite (zero skips), lint, tracked secrets and independent security review before pushing/deploy.
