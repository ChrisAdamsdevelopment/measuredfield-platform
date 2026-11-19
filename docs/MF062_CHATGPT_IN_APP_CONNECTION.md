# MF-062: Connect MeasuredField from ChatGPT — real deployment evidence

Date: 2026-11-17 (Arizona). Scope: connection verification, private plugin packaging, and release decision. This document describes the live production **app** separately from the not-yet-live OAuth **MCP resource**.

## What was actually verified

- Authorized Render API: service `measuredfield-production-web` (`srv-dbrk59jrjlhs73bha8pg`) exists, is not suspended, uses `ChrisAdamsdevelopment/measuredfield-platform`, and publicly serves `https://measuredfield-staging-web.onrender.com`.
- Latest live Render deployment when checked: `6c13a9928f805721be124e63c36813253ff9c959`, November 13, 2026. Local main was `cbb98cc` (MF-074), so the deployed version lags the development checkout. Do NOT deploy the entire current main merely to turn on MCP.
- HTTPS `/healthz` and `/readyz` both returned HTTP 200. HTTPS `/mcp` and `/.well-known/oauth-protected-resource/mcp` returned HTTP 404. This is a real running web app, but **not** a usable ChatGPT MCP endpoint.
- Render direct-service environment names include `MF_AUTH0_DOMAIN`, `MF_AUTH0_CLIENT_ID`, `MF_AUTH0_CLIENT_SECRET`. They do NOT include `MF_MCP_OAUTH_ISSUER`, `MF_MCP_OAUTH_JWKS_URL`, `MF_MCP_RESOURCE_URL` or the `MF_OIDC_LINK_*` names at the check time. Only names were printed, never values. Environment groups may add separate variables and were not inferred.
- Public Auth0 OpenID configuration responded and advertises authorization-code and S256 PKCE. No `registration_endpoint` appeared; resource-indicator support was not advertised. This is not proof it cannot be configured for pre-registered ChatGPT clients, nor proof that tokens can carry the required exact MCP audience. **Test issuance and aud before enabling customers.**
- Private personal plugin `plugins_6afcba53a45c8191b5c9511a991758aa`, MeasuredField v0.1.0, exists with four skills. It has no `mcp.json`; its skills do not grant access to first-party data. Local source extracted under `D:\plugin\measuredfield-growth-workspace`.

## Safe public boundary check

`python -B scripts/mcp_private_connect_smoke.py --live --json` calls exactly four public fixed URLs on the approved HTTPS host, does not follow redirects, does not fetch any customer data and never reads tokens or Render API secrets. It requires a 200 health and ready status, a 401 Bearer challenge with resource metadata at `/mcp`, plus a 200 OAuth protected-resource document with exact `resource`, HTTPS authorization server, and `mf:read` scope.

Passing this network-only check means **public server boundary ready**; it NEVER claims a human ChatGPT login, installed connection or revocation. Until all separate real-user OAuth tests complete, `chatgpt_plugin_connected` remains false. Use the no-network default mode for CI.

## Implementation route

1. **Complete independent security review.** MF-041, MF-044, MF-045, MF-046, MF-047, MF-050 and MF-051 are approved. MF-037 (OAuth resource) and MF-039 (tool metadata), MF-040 (revocation) remain review-gated as of this writing. Do not publish unreviewed identity code.
2. **Decide resource hosting.** The reviewed standalone factory is `uvicorn --factory app.mcp_plugin.server:create_app` from `app/mcp_plugin/server.py`. It is not currently mounted by `app/main.py`. A separate secure Render resource service costs hosting; mounting on the existing app requires its own HostRouter/ASGI lifespan, Origin, CSRF and path-isolation regression work. Choose one intentionally, and use a distinct source-controlled story.
3. **Configure OAuth provider and exact audience.** Register an Auth0 API resource identifier EXACTLY matching the final `https://<approved-host>/mcp`; ensure actual OAuth code+PKCE tokens use the identical resource audience and `mf:read`. The current MCP verifier rejects any mismatch. Do not assume Auth0 automatically interprets the OAuth `resource` indicator as an Auth0 `audience` value. Test actual authorize/token requests. Auth0 must have the appropriate scopes enabled.
4. **Register separate OAuth clients.** The private first-party MeasuredField owner-link client uses the callback `https://measuredfield-staging-web.onrender.com/app/connections/callback` and confidential credentials. ChatGPT needs **its own** preregistered OAuth client or a verified provider-supported CIMD/DCR route, with the exact callback from ChatGPT's connection flow; do not invent the callback or reuse the website login client. OAuth provider discovery should advertise supported S256.
5. **Configure separately scoped deployment variables.** `MF_MCP_OAUTH_ISSUER`, `MF_MCP_OAUTH_JWKS_URL`, `MF_MCP_RESOURCE_URL`, `MF_OIDC_LINK_CLIENT_ID`, `MF_OIDC_LINK_CLIENT_SECRET`, `MF_OIDC_LINK_AUTH_URL`, `MF_OIDC_LINK_TOKEN_URL`; keep `MF_SECRET_KEY` and restricted runtime `DATABASE_URL` server-side. Check tenant RLS and migrations first. Never log token or key values.
6. **Deploy after review, then probe.** Validate TLS, Render proxy Host and Origin behavior, 401 Bearer challenge, resource metadata, JSON tools list, and authenticated signed access by an owner linked via confirmed browser-session PKCE. A synthetic two-tenant account must demonstrate own-data read and cross-tenant refusal; a Disconnect must stop the same token. No provider writes, email sends, paid research or computer filesystem operations.
7. **Attach the existing personal plugin, not a duplicate.** Once the exact reachable `/mcp` endpoint works, add a portable `mcp.json` with `type: streamable-http`, exact HTTPS URL and verified `extensions.com.openai.auth` OAuth configuration; update the owned plugin with Plugin Creator and preserve the existing plugin ID and four skills. User completes ChatGPT's OAuth consent/installation; tools are then explicitly re-discovered. `D:\plugin` is the local source; merely extracting a ZIP does not connect a server.
8. **Keep publication separate.** Private connected, submitted to OpenAI directory, approved, and discoverable are distinct outcomes. Public listing requires domain verification, support/privacy pages and OpenAI review, not just a saved ZIP.

Current public service boundary result: **NOT READY**. No deployment or OAuth registration was performed under this MF-062 slice; no real customer records or login secrets were accessed.

Official references (checked 2026-11-17): https://developers.openai.com/plugins/build/auth ; https://developers.openai.com/plugins/deploy/connect-chatgpt ; https://developers.openai.com/plugins/build/plugins ; https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt .
