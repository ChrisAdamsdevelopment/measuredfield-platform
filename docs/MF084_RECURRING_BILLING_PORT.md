# MF-084 — Recurring billing code port to canonical MeasuredField main

**Date:** November 17, 2026 (Arizona). **Author:** GPT-6. **Status:** local code, independent review and production test-mode gates outstanding.

## Why this story exists

Older work on `feature/mf006-stripe-growth-partner-20261112` commit `41ac454` implements a Stripe subscription ledger and 100%-discount-aware checkout but remained on a stale branch. Current production `main` advanced by over 60 commits; merging the old branch wholesale would overwrite migrations 0005-0011, authenticated MCP and tenant/security improvements. The user asked to bring the real business online, but not incur unverified charges or create duplicate live Stripe customers.

## Files and exact scope

- `app/services/orders.py` and `app/services/payments.py`: code carried from the earlier implementation. Git diff between old common base and the current main was EMPTY for these two files, so branch code could be ported without overwriting new edits. Functions cover signed checkout, subscription created/updated/deleted, invoice.paid/invoice.payment_failed, bounded grace and matched invoice refund, idempotent event handling, and Stripe Price-backed Checkout with promotion codes.
- `app/models.py`: ONLY the BillingSubscription model and three settlement columns inserted into the current much larger model; current devices, OIDC, visibility, Census and tenant models preserved.
- `migrations/versions/0012_billing_subscriptions.py`: new migration revised from the old branch 0005 into the current Alembic chain. **down_revision=0011**; existing 0005-0011 untouched.
- `tests/test_subscription_reconciliation.py`: ported synthetic signed webhook and mock checkout tests; repaired one unused import. `tests/test_mf084_checkout.py`: added explicit adversarial amount math, invalid real-Price ID rejection without network and invoice subscription identifier cases.

## Hard release gates (do not treat code review alone as live billing)

1. Full disposable PostgreSQL suite and migration from 0011 to 0012 must pass with **zero failures or skipped tests**. Same forced RLS/runtime role discipline.
2. Independent Claude review, checking real Stripe API event shapes (especially newer 2026 invoice and subscription period fields), concurrent duplicate deliveries, cross-order binding, and entitlement revocation. Until reviewed, the code must NOT be merged/deployed.
3. Live Stripe account `acct_1TZYbe2LWll6Mkhu` has existing products and prices: Business Workspace `price_1Ub6rZ2LWll6MkhusWBI1ebk` and Growth Partner `price_1Ub6rh2LWll6MkhuYiaLXu2m` ($99/month each), with founder coupon `MF-FOUNDER-PILOT-2026` 100% forever capped at 5 redemptions and old once-only `GUIDEDLAUNCH`. **Do not create new products, coupons or founder customers**.
4. First Stripe connector in the current chat has **live mode only**. Obtain true TEST-mode access before any charge, renewal or cancellation lifecycle tests. Do not use a live card or recurring subscription as a fake test.
5. A webhook endpoint now exists: `we_1Ucwcm2LWll6MkhueYq34EgJ` for `https://spectracleanse.com/webhooks/stripe`; it was created through Stripe's authenticated connector, then immediately **DISABLED**. It only subscribes to `checkout.session.completed`. The endpoint's signing secret was **not yet stored** in MeasuredField's Render `MF_STRIPE_WEBHOOK_SECRET`. Current Render payment provider remains `none`; no checkout or webhook processing live.
6. Before enabling billing: configure the real webhook signature secret securely; subscribe to needed lifecycle events (checkout, subscription created/updated/deleted, invoice paid/failed, refund); verify actual supported Stripe event variants in test mode; implement owner-facing cancellation/update and no-surprise-billing; ensure access gates call current subscription status, not stale cached flags. Remove or disable test resources appropriately after verification.

## Other production launch work completed concurrently

- Approved GitHub source `a595e75` deployed on Render `srv-dbrk59jrjlhs73bha8pg`.
- MCP OAuth resource and JWKS settings installed; `MF_ENABLE_REMOTE_MCP=true`; live `/mcp` responds 401 Bearer challenge, metadata discovery 200, no public unauthenticated read. Signed-in Auth0 owner linking is NOT completed.
- Public Auth0 authorization/token discovery URLs set in Render env `MF_OIDC_LINK_AUTH_URL` and `MF_OIDC_LINK_TOKEN_URL`, but private `MF_OIDC_LINK_CLIENT_ID` and `MF_OIDC_LINK_CLIENT_SECRET` are NOT yet installed. Host allowlist normalized, deploy confirmed live.
- Owner operator account remains NOT created: previous Desktop Commander approval attempt returned client error, and current read-only PostgreSQL users count is zero.
- Existing free Render PostgreSQL has no verified complete backup, expires 2026-12-13 if not upgraded; user will pay upgrade before then. Until backup/restore proven, do not claim durable paying-customer production.

## Verification evidence

**Final full disposable PostgreSQL gate:** `python -B scripts/run_disposable_pg_audit.py --full-suite` → `CI_GATE_PASS`: **1,095 passed**, zero skipped/errors/failures, `FULL_POSTGRES_SUITE_EXIT=0`, and disposable database container stopped/removed. The earlier port-only run passed 1,087, before adding eight new tests. **Eight additional pure adversarial checkout tests** passed (`python -B -m pytest -q tests/test_mf084_checkout.py`). Ruff checked the 7 scoped Python files with no findings; compileall passed. **Disposable backup/restore drill:** `python -B scripts/backup_drill.py` → `BACKUP_DRILL_PASS`: 121 synthetic rows restored identically across 46 tables at Alembic `0012`, including 25 forced-RLS tenant tables. This drill also confirmed that an owner-role dump can silently omit tenant data; a full production restore still has NOT been verified. No real customer data, passwords, OAuth tokens or Stripe signing keys are committed or copied into this document.
