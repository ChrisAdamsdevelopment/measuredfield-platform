# MF-077 — Opt-in MCP route on the existing MeasuredField web service

**Date:** 2026-11-17 · **Status:** local source implementation; do not treat as deployed or ChatGPT-connected.

## Purpose

The approved MF-037 read-only MCP v2 server can be hosted at `/mcp` on the already-running MeasuredField Render web service without creating another paid resource. The normal marketing/owner web app continues routing through `HostRouter`; customers' separately published website hosts stay isolated.

**Actual production service at inspection:** `measuredfield-production-web` (Render `srv-dbrk59jrjlhs73bha8pg`), externally `https://measuredfield-staging-web.onrender.com`. Active deploy was SHA `6c13a992` (2026-11-13). Prior to this work, `/healthz` and `/readyz` returned 200, while `/mcp` and `/.well-known/oauth-protected-resource/mcp` returned 404.

## Opt-in design and fail-closed gates

- `MF_ENABLE_REMOTE_MCP` defaults to literal `false`. `true` is the **only** enabling spelling; a malformed flag refuses application startup instead of accidentally enabling.
- With `false` or absent, no MCP server is constructed or exposed. Existing app/site routing and health remain unchanged.
- With `true`, the server validates `MF_MCP_OAUTH_ISSUER`, `MF_MCP_OAUTH_JWKS_URL`, `MF_MCP_RESOURCE_URL` (a real public HTTPS `/mcp` URL), and requires its exact resource host to appear in `MF_APP_HOSTS`. Missing/untrusted configuration refuses the release at startup.
- The existing MCP SDK v2 `create_app()` factory retains its OAuth HTTP verification, JWT resource audience check, verified user link and per-tool `mf:read` metadata. This story adds **no** unauthenticated or write tools.
- Only `/mcp` and `/.well-known/oauth-protected-resource/mcp` on the exact validated MeasuredField app host are sent to the MCP ASGI app. A customer/foreign host receives 404 on those paths. All other paths route as before.
- MCP traffic is not passed through the HTML form CSRF middleware; it is protected by the MCP server's bearer authentication and strict Host/Origin middleware. The MCP session manager's own ASGI lifespan is entered and exited together with the existing FastAPI web lifespan.
- No production environment values, database credentials or owner account records are read by this source change. No Render deploy, GitHub push, OAuth provider registration or plugin package mutation is performed.

## Tests and release proof

- `tests/test_mcp_host_routing.py`: disabled mode, malformed flags, missing OAuth settings, Host isolation, customer-site separation, protected discovery, malformed/duplicate Host, and child app lifecycle with synthetic ASGI apps.
- Official MCP SDK 2.3.0 synthetic-client exercise: the **actual** MCP factory is mounted behind `HostRouter` and must return 401 for anonymous initialize, 200 for protected-resource metadata, 200 for an *explicitly synthetic* accepted bearer, and 404 for foreign application Host. The original MF-045 SDK probe separately confirms hostile Origin rejection. These are not real Auth0 tokens or live login.
- Full disposable PostgreSQL tests with zero skips, ruff and staged+tracked secret scans required before submit.
- Independent reviewer should check path routing, ASGI lifespan, middleware ordering, host confusion, endpoint metadata and no unauthenticated MCP capabilities. Do not self-approve.

## Production connection sequence (NOT executed by this story)

1. Complete independent reviews of all required OAuth/resource and account-link stories, including MF-039 and MF-040; obtain a separate owner-approved production release decision.
2. Register an Auth0 API with the **exact** MCP resource audience, configure scopes and verify Auth0 returns the expected `aud` on real authorization-code + PKCE tokens. The existing Auth0 application login settings are not a substitute.
3. Confirm the actual ChatGPT client's request Origin before relaxing the strict SDK allowlist. The current MF-045 guard accepts only the resource HTTPS origin, so a genuine ChatGPT Origin header might receive 403. Never add wildcard Origins or trust forwarded Origin headers; record and allow only independently verified exact origins in a separate reviewed security story.
4. Register the first-party MeasuredField owner-link OIDC client (server-side secret, callback `/app/connections/callback`) and a separate ChatGPT OAuth client using the correct callback and pre-registered client flow. Validate token and issuer discovery, TLS and CSRF/PKCE defenses with a synthetic owner.
5. Set the above `MF_MCP_*` and `MF_OIDC_LINK_*` variables in Render without printing values. Keep `MF_ENABLE_REMOTE_MCP=false` until the complete review and rollback plan. Verify production restricted PostgreSQL runtime role, migrations, tenant isolation and backup before deploy.
6. Deploy an explicitly pinned/approved commit through Render. Only when metadata, hostile-Host/Origin, 401 and two-tenant revocation tests pass, set `MF_ENABLE_REMOTE_MCP=true` and deploy the configuration.
7. Run `python -B scripts/mcp_private_connect_smoke.py --live --json`. It verifies the public boundary but **never** proves a ChatGPT login. Update the EXISTING private plugin `plugins_6afcba53a45c8191b5c9511a991758aa` to add a verified `mcp.json` with the working URL and OAuth configuration. User then authorizes ChatGPT interactively.
8. With the owner's real consent, prove `get_connected_profile`, authorized business reads, cross-tenant refusal, and immediate denial after account Disconnect. Record the evidence without any bearer token or customer record contents.
9. Do not equate private plugin install with public directory submission. Public listing requires additional verification and OpenAI review.

**Rollback:** Set `MF_ENABLE_REMOTE_MCP=false` and redeploy the last independently approved release; this removes the route while retaining the preexisting web app. A production rollback must also revoke any compromised OAuth clients/tokens separately.
