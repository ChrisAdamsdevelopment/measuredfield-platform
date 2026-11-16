# MF-040 — Owner ChatGPT connections: personal list and immediate disconnect

**Local-only release candidate. Not connected to live ChatGPT.** MeasuredField's separate OAuth 2.1 authorization/PKCE identity-provider enrollment is not yet configured. This is a practical owner control for the future read-only plugin.

## Owner journey

Open the MeasuredField private workspace and choose **ChatGPT Connections** in the left navigation. This page shows only connections belonging to the *signed-in person*, including their identity-provider hostname, connection date, active/disconnected state, and a **Disconnect this account** button. Team members signed into the same business do **not** see or revoke each other's personal ChatGPT identities. A disconnected link can no longer be used for business data access via MF-037.

The page does not offer a fake Connect button: connecting requires the separately implemented and tested owner-verified OAuth flow, an approved staging HTTPS endpoint and a ChatGPT-side installation. Files, local computers and future AI search billing are separate permissions, not automatically enabled by a connected identity.

## Security

- app/routers/mcp_account_settings.py uses the existing signed-in browser session. JSON routes use current session plus X-CSRF-Token; the page form uses standard workspace CSRF proof. POST also requires an explicit confirmation.
- Every database query filters McpIdentityLink.user_id to the **server-resolved signed-in user**. Never trust an org_id, user_id, OAuth subject, email, bearer token or model-provided identity.
- A link owned by another account, or a made-up identifier, returns the same generic HTTP 404. No raw OAuth subject, email, token, JWKS, or customer inquiry is returned on the list or page.
- Revocation sets revoked_at under a row lock; repeats are safe and return disconnected_now=false. The MF-037 identity resolver filters on revoked_at at each authorized invocation, so a still-valid identity-provider JWT is insufficient to continue.
- Account links are **personal identity records**; local Windows computer grants remain separately under /app/computers and are not changed.
- No OAuth link creation, external network call, outbound email, publishing, payment, browser automation or production setting is enabled.

## API and UI surfaces

- GET /app/connections — accessible owner page, linked to the existing workspace navigation.
- POST /app/connections/{link_id}/disconnect — CSRF form with confirm=DISCONNECT; redirects with signed status notice.
- GET /api/v1/mcp/account-links — user-scoped JSON, no current organization required.
- POST /api/v1/mcp/account-links/{link_id}/disconnect — user-scoped JSON, CSRF header and confirm=DISCONNECT CHATGPT. A wrong user or unknown ID is 404.

All are backed by the existing platform-level mcp_identity_links table. No new schema or extension is required.

## Verification

Run python -B scripts/run_disposable_pg_audit.py --full-suite with disposable PostgreSQL and strict zero-skips. Focused real-PG regression tests cover browser session, CSRF, explicit confirmation, two-person information isolation, exact revocation, idempotency, and immediate denial through ReadOnlyBusinessGateway.

This is a GPT-6 self-reviewed implementation while Claude and Argon are unavailable. It is **not** independent production approval. No public URL has been registered.
