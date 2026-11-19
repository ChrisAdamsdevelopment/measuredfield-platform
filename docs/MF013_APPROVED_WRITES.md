# MF-013 — Scoped ChatGPT write tools and first-party owner approvals

2026-11-19 Arizona. SOURCE DEVELOPMENT ONLY. This is not a claim of a successfully connected ChatGPT OAuth client, active customer email send, or deployed feature.

## What is implemented locally

The existing read-only OAuth MCP server still requires signed bearer JWT validation for exact trusted issuer, resource audience, expiry and read scope; immutable OIDC owner linking and revocation remain mandatory. The new `save_inquiry_reply_draft` tool is a controlled write action requiring **BOTH** `mf:read` and `mf:drafts.write`. It is not inferred from a read-only connection or model-supplied user ID. ChatGPT must obtain explicit Auth0 authorization for the additional scope when available.

The official MCP SDK v2 tool descriptor marks the operation `readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`. The new tool is a bounded first-party database write; it has no email, customer-contact, public-post, deployment, payment, file, or computer-execution capabilities.

A verified signed subject is checked against the current MeasuredField identity link, enabled account, owner/admin membership, exact business UUID and source inquiry inside forced PostgreSQL RLS. A stable client-supplied request ID is used ONLY for idempotency, never identity or authorization. Reuse with different body, lead or OAuth client is rejected. The database enforces a unique per-business/per-author request ID, one actual tenant-safe inquiry FK, body length and status constraints. Invalid/rate-limited requests fail closed.

New migration **0015**, after pending-production migrations **0013 and 0014**, creates `mcp_action_drafts` with ENABLE + FORCE row-level security and a composite same-business lead FK. It updates the tenant export/deletion table mapping. No previous customer data is migrated or touched.

The separate first-party `GET /app/mcp/approvals` page lists proposed replies for the signed-in business owner. The `POST /app/mcp/approvals/{draft_id}/decide` route requires owner membership, browser-session CSRF, the explicit `approved` or `declined` choice, a one-time draft state transition, and an audit event without message text. Approval **does not send** the reply. The page clearly tells the owner to copy any approved message and send it manually from their business email. A connected ChatGPT plugin cannot call the browser session's approval endpoint using OAuth credentials.

## Verification

- Ruff and Python compilation passed for the implementation.
- 45 existing OAuth/plugin pure tests passed after extending read/write metadata compatibility.
- Seven synthetic real PostgreSQL integration tests passed against a disposable restricted runtime role: tenant isolation, write-scope denial, wrong business/lead, role downgrade, revoked OAuth link, disabled user, request replay and immutable body, CSRF, owner decision, cross-owner denial, and zero customer outbound contact.
- Official isolated MCP SDK v2 listed nine tools and exposed the write tool metadata with both scopes and readOnlyHint=false. Existing profile tool remained read-only.

## Release gates (all still pending)

1. Independently review and run the full **1158+** real PostgreSQL regression after source changes. RLS must pass with separate schema-owner/runtime roles and zero skipped tests.
2. Do not apply migration 0015 to production before verified database recovery and prerequisite migrations 0013 and 0014.
3. Register and authorize `mf:drafts.write` specifically on the approved Auth0 MCP API resource; verify code+PKCE issuance and exact resource audience, separate ChatGPT client registration, signed short-lived tokens, current OIDC link, and revoke behavior. No secret or token in source or logs.
4. Private personal ChatGPT plugin remains not installed in this account and does not yet contain a verified `mcp.json`. Preserve existing plugin ID and four skills, then explicitly install/connect with user consent and test the real signed JWT against two live sandbox tenants; never confuse endpoint 401/read metadata with a completed connection.
5. Even after code deployment, do not claim that approval sends email or publishes. Building a confirmed customer send/publish workflow requires verified sending identity, a separate consequential-action approval, provider idempotency, audit and safe retry semantics. Avoid sending real messages to test addresses.
6. Rollout must use a pinned reviewed commit, verified backup and rollback, minimal scope consent, private connection testing, and mobile owner UI QA. No unattended push, Render deployment, Auth0 update, live charges or customer contacts.

This scoped reply draft feature is a foundational write capability, not the full interactive customer-message or publishing suite.

## Independent security review corrections (2026-11-19)

Initial Claude review requested three low-severity corrections. The collision/IntegrityError recovery now checks the identical owner/admin role AND can(role, "leads.write") permission as the main path. Model-drafted content containing bidi overrides or invisible zero-width formatting controls is rejected before storage; the owner's approval page also warns about misleading lookalike names and URLs. Normal Spanish accents remain allowed.

The no-org membership preflight is supported by an EXISTING database RLS policy defined in migrations/versions/0002_row_level_security.py, not by new migration 0015: CREATE POLICY own_memberships ON memberships FOR SELECT USING (app_org() IS NULL AND user_id = app_user()). The same migration defines the strict tenant_isolation policy requiring app_org() for UPDATE, and member_can_see on organizations. This explains why the gateway reads membership without a tenant first, sets the selected tenant, then locks and rechecks membership to authorize writing.

An already valid owner token containing mf:read but missing mf:drafts.write may receive an MCP tool-result mcp/www_authenticate Bearer insufficient_scope challenge naming both scopes and the exact trusted HTTPS protected-resource metadata. Invalid, unlinked or revoked read tokens do not receive a scope-escalation prompt. This is a mechanism for external ChatGPT reauthorization, not evidence that Auth0 is configured, consented, or live.

## Final local QA and independent review — November 19, 2026 (Arizona)

- **Full 1,172/1,172** isolated, restricted PostgreSQL tests PASS, zero skipped/failures/errors. CI_GATE_PASS; test Docker Postgres removed. Official MCP SDK v2 tool-invocation smoke passes both paths: revoked synthetic read-only identity is **denied without authorization challenge**, currently linked synthetic read-only identity receives **incremental mf:drafts.write consent challenge** but performs no write.
- Pure plugin/OAuth metadata suite 46 passed before final correction, subsequent targeted metadata 9 passed. Ruff and Python compilation PASS. Independent Claude reviews: initial/second requested low corrections, final pass **APPROVE** (AGENT_BOARD/CLAUDE_MF013_FINAL_DELTA_OUTPUT_20261119.txt).
- Actual live MeasuredField host `https://measuredfield-staging-web.onrender.com`: health=200 and ready=200, but /mcp=404 and protected-resource metadata=404 on November 19. Previous 401/200 observation on another domain was unrelated and does **not** prove this product is connected.
- Authorized Render read-only preflight: **blocked** (live/tested source differ, production branch changed, required direct MCP/OIDC configuration incomplete; environment-group inheritance unverified). No production deployment, migration, Auth0 registration, provider charges, actual customer email, or ChatGPT installation occurred.
- Private source work is complete for **saving** review-only replies. Actual customer **sending**, real third-party API write controls, and owner-verified publishing are separate future workflows, not claimed implemented.
