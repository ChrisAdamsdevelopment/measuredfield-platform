# MF-037 — ChatGPT Plugin OAuth resource server and first-party read-only tools

**Status:** LOCAL, OFFLINE, NOT REGISTERED WITH CHATGPT, NOT DEPLOYED, NO ACCOUNT CONNECTED.

MeasuredField's ChatGPT integration is a remote **MCP v2 plugin**, not a GPT Action. The existing MF-012 tenant-aware gateway is reused. The connector does not make OpenAI API calls and does not require an OpenAI model API key merely to respond to ChatGPT's authorized tool calls.

## What is implemented

- app/mcp_plugin/config.py: HTTPS-only explicit config for a real authorization provider issuer, its signed JWKS and exact MeasuredField /mcp resource URL. Refuses local/test domains, IP literals, credentials in URLs and missing setup. No OAuth credentials are hardcoded or read from customer files.
- app/mcp_plugin/tokens.py: PyJWT RS256/ES256 signature verification using an explicitly configured issuer JWKS URL, issuer/resource audience/expiry/issued-at/scope checks, rejection of token-supplied key URLs, and a fresh owner account-link check. This is the resource-server half of OAuth, **not an OAuth authorization server**.
- app/models.py and migrations/versions/0009_mcp_identity_links.py: a platform-level unique (issuer, subject) -> local User.id link, only valid when verified_method=oidc_pkce_owner and not revoked. The schema intentionally does **not** create or offer any public self-link API.
- app/mcp_plugin/links.py: reads the platform link on every verification and on each existing MF-012 gateway tool. Disabled users, revoked links and pre-link tokens fail closed. Tenant-specific business authorization stays in app/mcp_connector/gateway.py under PostgreSQL FORCE row-level security.
- app/mcp_plugin/server.py: official Python SDK v2 MCPServer, OAuth HTTP authentication middleware, Streamable HTTP app and read-only tools: list_my_businesses, get_business_overview, list_inquiries, get_inquiry, and preview_reply (not sent, not saved). No write action, file read, shell, payment, email or publish tool is exposed.
- requirements.txt: pin installed/tested mcp==2.3.0 and PyJWT[crypto]==2.15.1. This is for new installs/deployments; do not copy the reference virtualenv into Git.
- tests/test_mcp_plugin.py and tests/test_pg_mcp_plugin.py: signed synthetic tokens, adversarial JWT claims and headers, revocation, uniqueness, invalid unverified binding methods and real tenant isolation.

## Verified SDK behavior

The isolated SDK v2.3.0 reference venv at D:/GrowthEngineeringVenture/15_INTEGRATION_WORKBENCH_20261111/CHATGPT_CONNECTOR_WORKBENCH/.venv is used only for local protocol verification.

Standalone factory: uvicorn --factory app.mcp_plugin.server:create_app

**DO NOT launch an externally accessible server until an approved real OAuth provider has been configured and all release gates pass.** The factory raises when its environment variables are absent.

Local SDK v2 test includes the actual Streamable HTTP route /mcp. The test makes an in-process request without an Authorization bearer and asserts HTTP 401. The SDK also exposes /.well-known/oauth-protected-resource/mcp for discovery. Nothing is reachable on a public hostname yet.

## Explicit remaining steps for a real ChatGPT connection

1. Choose/configure an established OAuth 2.1 identity provider (for example Auth0) under a MeasuredField-controlled domain. Require PKCE S256 and issuer metadata / token endpoint; prefer CIMD support or DCR. Do NOT invent client IDs or accept client-supplied MeasuredField user IDs.
2. Implement a first-party authenticated MeasuredField account linking journey: owner signs into MeasuredField and completes OAuth authorization-code + PKCE/OIDC with that provider, verifies subject and explicitly consents to read-only connection. Only after verifying *both* sides can the server create McpIdentityLink. Provide a Disconnect control setting revoked_at. No direct insert via model or AI interface.
3. Configure environment with MF_MCP_OAUTH_ISSUER, MF_MCP_OAUTH_JWKS_URL, MF_MCP_RESOURCE_URL. JWKS URL must share the trusted issuer hostname; the resource URL must be an approved HTTPS hostname ending in /mcp. Never paste secrets into board posts or prompts.
4. Deploy the standalone MCP server to a separately approved staging HTTPS domain with pinned production packages, correct startup lifespan, verified TLS and runtime PostgreSQL role. Review restricted database grants, egress restrictions, logging, consent/audit, token revocation, rate limits, origin and CORS policies and default-deny behavior. No customer credentials or production domains should be used to verify the first release.
5. Check protected-resource discovery and authorization-server metadata. ChatGPT plugin registration requires a reachable authorized MCP URL and a real user OAuth flow, not merely an installed OpenAI Developers plugin. The user must explicitly connect/approve in ChatGPT.
6. Test one consenting synthetic business's tool list, real token exchange, read-only results, revoked membership, disabled owner and cross-tenant access, then complete independent Argon/Claude review. No live customer records are necessary for readiness testing.
7. Only after read-only trust works, scope separately owner-confirmed write tools (draft / send / publish), and a different approved desktop secure-tunnel feature. Never grant blanket D: drive access.

## About custom GPT Actions

As of November 2026, OpenAI is transitioning from custom GPTs to Plugins. New custom GPT creation is unavailable for personal ChatGPT accounts. Existing editable GPT Actions can still use an OpenAPI schema where supported, but are not the primary integration plan for MeasuredField. A GPT Action would require its own verified OAuth and domain-backed HTTPS API; it would NOT bypass any of the above account/tenant security work.

Sources:
- https://developers.openai.com/plugins/quickstart
- https://developers.openai.com/plugins/build/mcp-server
- https://developers.openai.com/plugins/build/auth
- https://help.openai.com/en/articles/9442513-configuring-actions-in-gpts

## No-impact boundaries

No Render services, real tenants, user data, OpenAI key, payment, GitHub external repository, public DNS, email or desktop agent changed by MF-037. No claimed plugin connection or simulated business evidence.
