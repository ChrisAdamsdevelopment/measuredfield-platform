# MF-041 — owner-verified ChatGPT identity enrollment with OIDC and PKCE

**Status: Local implementation only. No real provider, ChatGPT account, domain, or user was linked. Never deploy as authenticated production without an independent security review and configured provider.**

## Why this exists

MF-037 verifies tokens presented to the read-only MCP resource server, MF-039 supplies ChatGPT-compatible tool permissions and profile metadata, and MF-040 lets a signed-in owner revoke their own links. MF-041 creates the missing trusted enrollment route. An email address, model-provided user ID, query-string subject, or HTTP bearer presented in isolation does not prove identity ownership.

This is a first-party *owner-account registration* flow, separate from ChatGPT's OAuth client flow to the MCP resource server. Both must use the same identity issuer and subject mapping; neither is a substitute for the other.

## Connection flow

1. A person with a signed-in MeasuredField workspace session opens ChatGPT Connections. Until approved provider settings are configured, the UI says linking is unavailable and exposes no Connect button.
2. The person explicitly chooses **Verify and connect my account (read only)**. The existing form CSRF token and workspace membership gate are enforced. Starts are limited to eight per 15 minutes per user.
3. MeasuredField creates a random OAuth state, nonce and PKCE verifier. The database stores only state/nonce SHA-256 hashes, a Fernet-encrypted code verifier (derived from the MeasuredField server-side secret with a purpose-specific key context), user ID, exact browser session ID, issuer, and 10-minute expiry. It never stores a plaintext code verifier, ID token or OAuth access token. The browser is redirected to the configured authorization endpoint with S256.
4. The callback requires the SAME signed-in user/session. A PostgreSQL row lock consumes the state one time, clears the encrypted verifier and commits that consumption BEFORE any provider request. An expired, replayed, forged or cross-session callback fails closed.
5. MeasuredField exchanges the provider authorization code over HTTPS with the pinned provider token endpoint, with code verifier and registered confidential client credentials. Redirects are refused. The returned ID token is verified against issuer-scoped signing keys (RS256/ES256 only), signature, issuer, audience/client ID, expiry, issued-at, nonce and authorized party. Nothing from the browser can determine the subject.
6. The verified issuer/subject can be linked only to this signed-in user. A link already assigned to another user (even revoked) cannot be silently transferred. The same owner may reconnect a previously revoked link; this refreshes the consent timestamp so older resource tokens cannot revive it.
7. The owner can use the existing Disconnect button. MF-037 rechecks the current link on each read-only tool invocation.

## Required private staging environment

All environment names are installation configuration, not secrets to paste into Git, chat or task-board messages:

- MF_MCP_OAUTH_ISSUER: exact OIDC issuer also used by the MCP resource server.
- MF_MCP_OAUTH_JWKS_URL: trusted HTTPS JWKS on the same hostname as issuer.
- MF_MCP_RESOURCE_URL: exact HTTPS ending in /mcp for the resource server.
- MF_OIDC_LINK_CLIENT_ID: registered MeasuredField **first-party web app** client ID at that issuer (NOT the ChatGPT OAuth client ID).
- MF_OIDC_LINK_CLIENT_SECRET: confidential first-party web app secret, kept only in deployed secret storage.
- MF_OIDC_LINK_AUTH_URL: the issuer's pinned HTTPS authorization endpoint.
- MF_OIDC_LINK_TOKEN_URL: the issuer's pinned HTTPS token endpoint.
- MF_PUBLIC_BASE_URL: MeasuredField private workspace HTTPS base, without a path. The redirect is derived as <base>/app/connections/callback and this exact URI must be approved by the identity provider.
- MF_APP_HOSTS: contains the workspace hostname, with no localhost entries in staging/production.
- MF_SECRET_KEY: random server secret at least 32 characters, distinct from the provider secret. Key rotation cancels pending enrollment challenges.

All provider URLs must use HTTPS on the configured issuer hostname, with no query/fragment or unsupported port. The callback is HTTPS on the configured MeasuredField app hostname. A dummy localhost or example/test issuer cannot satisfy production configuration. The provider **must actually support OIDC ID tokens, authorization-code flow and PKCE S256**. The ChatGPT OAuth client needs its own separately configured registration mechanism (CIMD, DCR or predefined client), resource audience echo and advertised S256 per OpenAI Plugins requirements.

This component does not implement OAuth authorization-server metadata, JWKS publication, token issuance or ChatGPT installation. Do not interpret this implementation as the live account connection being complete.

## API surfaces

- GET /app/connections — display current personal links and appropriate Connect button only when fully configured.
- POST /app/connections/start — signed-in workspace membership + CSRF + explicit read-only confirmation; returns HTTP 303 to trusted configured OIDC authorization endpoint.
- GET /app/connections/callback?code=...&state=... — SAME authenticated browser session, single-use state, provider code exchange, verified ID token; redirects back to /app/connections with generic signed notice.
- Existing personal disconnect and listing remain unchanged. No model-callable linking tool or arbitrary user account endpoint is added.

## Test and security boundaries

- The synthetic-provider unit suite asserts S256, no identity supplied in authorize args, verification of signed ID token and rejection of malicious claims, expired tokens, malformed provider responses and header key-injection attempts.
- Real PostgreSQL tests exercise the UI and session/CSRF/owner boundary, single-use/replay, expiry, cross-session, two-user issuer-subject conflict, malicious JWTs, and reconnect-after-revocation.
- No provider call is made from the test suite; the signed ID token is synthetic and the token exchange is an injected in-memory response.
- Review before deployment: identity-provider metadata and callback allowlist, host validation, transport trust, key rotation, failure/retry semantics, malicious providers, multi-worker behavior, session revocation while exchanging code, event logging without token contents, cleanup of expired challenges, and production rate limiting.

References: https://developers.openai.com/plugins/build/auth and https://openid.net/specs/openid-connect-core-1_0.html

Implementation authored/tested by GPT-6 while other agents are usage limited. **Self-review is not peer approval.**
