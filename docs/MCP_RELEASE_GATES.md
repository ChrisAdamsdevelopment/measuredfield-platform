# MF-013: Authenticated ChatGPT MCP release gates (November 12, 2026)

## Implementation boundary
- **Existing approved gateway MF-012:** in-process read-only `ReadOnlyBusinessGateway` with owner/user membership, forced-RLS tenant isolation. Independently approved by Antigravity Argon High on 2026-11-12; it does not claim to provide HTTP/OAuth.
- **New MF-013 work:** `app/mcp_auth/identity.py` validates a trusted issuer-signed RS256/ES256 OAuth access token against operator-controlled JWKS, exact HTTPS issuer/resource audience, expiry, client identity, `mf:read`, token ID, and a required revocation lookup. No live issuer, token, business mapping or key is in source control.
- **New MCP transport composition:** `app/mcp_auth/server.py` uses official Python MCP SDK 2.3 Streamable HTTP and exposes *only* read-only SQLAlchemy tools. It is intentionally not mounted in the public FastAPI application; `build_app()` fails startup without env and rejects all tokens until a real revocation/subject service is injected.
- **Not yet implemented:** a configured OAuth 2.1 authorization server/provider with PKCE, registration/DCR/CIMD, OAuth user-to-MeasuredField durable linking/revocation store, in-ChatGPT connection test, owner-approved write action execution, real staging deployment.

## Canonical URLs and secrets still needed (names only)
- `MF_MCP_RESOURCE_URL`: exact public HTTPS MCP resource ending in `/mcp`.
- `MF_MCP_ISSUER`: identity provider's exact HTTPS OAuth issuer base URL.
- `MF_MCP_JWKS_URL`: identity provider's trusted HTTPS JWKS endpoint.
- `DATABASE_URL`: restricted tenant runtime database role, **never** schema owner or superuser.
- OAuth provider's client configuration, redirect URI allowlist, signing key rotation and revocation connection settings should be stored only in Render's secured secrets or the identity provider.

Do not copy OAuth JWTs, client secrets, long-lived API keys, or private customer emails into GitHub, logs or tests.

## OAuth flow and binding
1. ChatGPT discovers `/.well-known/oauth-protected-resource` and the OAuth issuer metadata (authorization code + PKCE S256, client metadata document/DCR as configured). Resource parameter must be bound through consent and token issuance.
2. User signs in at authorized IdP and consents to `mf:read` for their own MeasuredField account.
3. Identity provider signs short-lived token; MCP SDK verifies it with `OAuthJWTVerifier`, checking signature, issuer, audience, expiry, scope, client and **revocation** on each request.
4. Subject resolver maps `(issuer, external sub)` to a previously verified MeasuredField `User.id` via a DB-bound linking record, never by searching email from a token or accepting a client-supplied `org_id` as authority. If missing, deny.
5. Tenant gateway checks user active, membership and RBAC for each requested `business_id` under PostgreSQL RLS.
6. Tool returns evidence-labelled first-party information. Lead message body and uploaded documents are UNTRUSTED DATA, not instructions to run further tools.

The MCP SDK handles Streamable HTTP transport and bearer protection. **No OAuth credentials, no subject-link mapping and no revocation store are configured yet.** The standalone factory therefore denies all private access.

## Approved write design (not shipped)
Separate `mf:propose` scope allows creation of a **draft** action proposal. A model cannot send email, publish websites, create Stripe charges or deploy Render services just by issuing a tool command.

A write proposal must contain a canonical action payload and SHA-256 hash, target business/resource, concrete before/after diff, cost estimate, expiration and who requested it. The signed-in owner must view this in the MeasuredField UI and explicitly approve the **exact hash** in a server-side transaction. The executor rechecks membership, connected provider scopes, authorization freshness, cost cap and idempotency key before performing exactly one action. Replays and changed payloads fail. Provider result is audited, and reversible actions offer rollback. The real `ConsentReceipt` persistence and write-action executor are a later MF-013 stage.

## Tests before public availability
- Token verifier synthetic tests: valid RS256 signature, forged key, wrong issuer, wrong audience, expired, missing scope, revoked grant, failed revocation lookup, JWT-supplied JWKS URL ignored.
- MCP transport: no Authorization -> 401 with WWW-Authenticate and resource metadata, protected metadata contains exact resource/issuer, only read-only tools advertised, no unauthenticated stdio bypass.
- Tenant: user A may not access org B, inactive user, revoked membership, suspended org, malformed UUID, and prompt injection carried in lead contents.
- Authentication: real IdP login, PKCE S256, callback issuer `iss`, audience, issued/rotated JWKS, token expiration, refresh, logout/disconnect, device/browser login, invalid client_id, callback replay.
- Deployment: stable public HTTPS on dedicated Render Web Service, trusted host validation, proxy headers, backups and per-request error logs without sensitive payloads.
- ChatGPT: install custom plugin and exercise profile/read tools, permission prompt, account disconnect/reconnect, denial messages, no mismatched tenant, no writes without approval.

## Deployment defaults
Run in a **separate MCP Web Service** built from the canonical public source, not the PBC site and not a local ngrok tunnel. Install isolated dependencies:
`python -m pip install -r app/mcp_auth/requirements-mcp.txt`
After OAuth and linking are fully wired, the ASGI service factory command is:
`uvicorn app.mcp_auth.server:build_app --factory --host 0.0.0.0 --port $PORT`
At present, the unconfigured factory should be used only to validate metadata and fail-closed behavior, not submitted as a functioning customer plugin.

## References (verified 2026-11-12)
https://developers.openai.com/plugins/build/auth
https://developers.openai.com/plugins/build/mcp-server
https://py.sdk.modelcontextprotocol.io/run/authorization/
https://py.sdk.modelcontextprotocol.io/run/asgi/
