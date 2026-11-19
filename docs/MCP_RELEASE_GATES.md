# ChatGPT MCP release gates — current project
## Verified local code
- MF-087 direct Stripe purchase fulfillment: commit 5563e78, 1,151 PG tests, Claude approved; NOT pushed or deployed.
- MF-013 scoped OAuth draft-writing and first-party owner review: complete locally, three independent Claude review passes ending **APPROVE**; 1,172/1,172 full disposable real PostgreSQL tests PASS, zero skipped/failures; official MCP SDK v2 dynamic revoked-link/read-scope challenge PASS. No customer messages sent. Production not deployed.
- **Latest read-only host probe, 2026-11-19:** the approved MeasuredField host `https://measuredfield-staging-web.onrender.com` returns `/healthz` and `/readyz` **200**, but `/mcp` and its OAuth resource metadata **404**. It is not a connected ChatGPT server. A previously observed 401/200 combination belonged to a different project domain and MUST NOT be counted as MeasuredField OAuth readiness.
- Private MeasuredField ChatGPT plugin exists as package v0.1.0 but is not installed in current ChatGPT account; its earlier package has no usable remote mcp.json.

## External blockers
- Verify live database backup/recovery; then migration 0013 owner checkout, 0014 Payment Link claims, 0015 OAuth drafts. 2026-11-19 live Render read-only preflight: `release_preflight=blocked`, `LIVE_AND_TESTED_SOURCE_DIFFER`, `PRODUCTION_BRANCH_CHANGED`, and `REQUIRED_MCP_CONFIGURATION_NOT_PRESENT`. Missing direct env names were reported for MF_CENSUS_API_KEY and first-party MF_OIDC_LINK_CLIENT_ID / MF_OIDC_LINK_CLIENT_SECRET; shared env-group inheritance remains unverified.
- Reconcile/push/deploy source only after controlled release, not blindly to the diverged Render branch.
- Verify live webhook with checkout.session.expired after deploying the revised handler, plus proper Stripe test-mode lifecycle, voluntary $0 promotion test and Billing Portal.
- Register real ChatGPT OAuth client (not website client) with Auth0; verify resource audience exactly, S256, and owner consent. Add only read and separately consented draft-write scopes. Test Connect, tools, and Disconnect with real signed tokens.
- Independently review each write tool, tenant/RLS/idempotency and owner approval UI. Do not enable direct send, website publish, computer/filesystem write or paid research without separate permission and completed tests.
- Public App/Plugin directory submission remains a separate user/organization approval process.
