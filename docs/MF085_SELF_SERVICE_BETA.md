# MF-085 — Self-service founding beta signup

Owner wants Andrew (construction business) and a second independently owned cleaning business to register themselves, verify email, enter separate private workspaces and later connect their own ChatGPT accounts.

## Implementation
- Public `/signup` no-card form, linked in site nav. Minimal business name, industry, owner name, email, terms/privacy consent and honeypot. `/signup/thanks` provides generic acknowledgment.
- If email provider is unavailable, no org or invite is created. If available, POST creates a separate `Organization(plan='founding_beta')` with explicit `billing.paid=false`, **no Stripe customer/order or subscription**.
- Existing `queue_auth_email` stores only unusable placeholder, mints one-use 72h invitation link at provider delivery. Existing `/accept-invite` requires proof of control of the email, creates member role owner, then signs in using secure HttpOnly session.
- Database-atomic IP and email-hash rate limits, duplicate pending invite suppression, no enumeration, no stored raw tokens, no verified claim on an arbitrary website/domain.
- Invitation acceptance and all business-data access go through existing forced PostgreSQL RLS and membership checks. No cross-business sharing; no PBC records imported.
- Beta is a **pilot, not a forever free Stripe entitlement**; owner should communicate feature maturity and database-recovery posture.
- Resend live sender settings provisioned separately on Render. A mail-provider acceptance receipt does not prove inbox delivery; inspect outbox and real signup flow before claims.

## QA and release
Run `python -B -m pytest -q tests/test_pg_beta_signup.py` under disposable restricted real PostgreSQL, `python -B scripts/run_disposable_pg_audit.py --full-suite`, Ruff and tracked-secret scan. Peer review required before push and live deploy. Confirm `MF_INLINE_JOBS=true` on single Render web instance (or run worker) so invite messages are actually dispatched. E2E smoke should use owner-authorized test emails only, never expose invite tokens to logs or chat.

## ChatGPT boundary
This story does NOT enable ChatGPT OAuth. The deployed /mcp protected resource is not enough to link an identity; Auth0 client/audience/redirect and consent must be configured and verified independently, and ChatGPT plugin/app installed or explicitly shared with testers.
