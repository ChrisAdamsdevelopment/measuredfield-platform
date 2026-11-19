# MF-091: Existing founder beta workspace -> Stripe Checkout (same app)

Objective: The signed-in OWNER can start the existing $99/mo Business Workspace Stripe Checkout with the existing promotion-code input enabled. A 100%-off founder code results in a zero-dollar subscription when accepted by Stripe; no separate beta copy or fabricated assessment is created.

Security contract:
- Only the existing selected organization from a verified server-side session, never a submitted org/customer ID. CSRF, owner role, rate limiting and organization row lock.
- Reuse the canonical offer and registered Stripe Price. Return URL does not grant access; signed webhook must bind exact server-created Checkout Session ID to the order and later verify subscription status/end period.
- New schema 0013 allows assessment_id NULL for authenticated owner-origin orders. Existing assessment-based orders remain unchanged; any future direct Payment Link work should build on this migration (0014+).
- Existing order.provision() sees org_id and MUST NOT create another org or invite. Display owner subscription status from active_subscription_for rather than stale plan or paid display snapshot.
- Pending session prevents duplicate checkout starts while unpaid. Failed Stripe creation marks a canceled order with no entitlement. After 24 hours pending sessions can be retried only if Stripe session expiration/closure is independently verified (release gate).
- Signed no_payment_required status with pre-discount subtotal matching stored approved price is required for $0 checkout. No entitlement from a coupon or redirect alone. Real customer payment processing and Stripe test-mode billing require separate QA.
- Test-only fake Stripe and disposable PostgreSQL multi-tenant checks, never charge, send real email, or access PBC business data. Requires independent security review and explicit production release after CI.
