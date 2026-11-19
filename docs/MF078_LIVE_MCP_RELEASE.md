# MF-078 — Controlled Live MCP Release and Plugin Connection

**2026-11-17 | Live Render metadata checked, no deployment performed.**

The source-side OAuth MCP server and source-labelled Census tools have passed local tests and independent review (MF-037/039/040/041/045/050/051/062/075/077). **The ChatGPT personal plugin remains skills-only and is NOT connected to the live MeasuredField MCP endpoint.** Do not confuse a healthy website with OAuth readiness.

## Proven current production state

The authorized Render API reports:
- Service `srv-dbrk59jrjlhs73bha8pg`, `measuredfield-production-web`, HTTPS host `https://measuredfield-staging-web.onrender.com`, status live, deployed commit `6c13a9928f805721be124e63c36813253ff9c959`.
- Auto-deploy is off; Render deploy branch is `feature/measuredfield-asset-production-20261112`, NOT local `main`.
- Git ancestry check at MF-075 development commit `0f201db`: local `main` had **55 commits absent from live**, while the live branch had **two commits absent from main**. They have diverged. Updating the Render branch or force-pushing without reconciliation risks overwriting features.
- `/healthz` and `/readyz` were HTTP 200. `/mcp` and `/.well-known/oauth-protected-resource/mcp` were both HTTP 404.
- Direct Render service env-variable **names** include the existing MF_AUTH0_* web-login settings but no `MF_ENABLE_REMOTE_MCP`, `MF_MCP_*`, `MF_OIDC_LINK_*` or `MF_CENSUS_API_KEY` at inspection. A second **name-only** check confirms canonical `MF_CENSUS_API_KEY` exists on the separate `srv-d84udt57vvec73filv6g` service, **not** directly on MeasuredField. Do not copy or move the key without a controlled, owner-approved environment change. Environment-group inheritance was **not** checked: verify it before declaring credentials missing. No key **values** were printed, moved or logged.
- The existing Auth0 issuer published OpenID discovery with authorization code flow and PKCE S256. Its discovery did not advertise DCR or resource indicator support. The actual provider client registration and required `aud` for the MCP resource have **not** been verified. Auth0's existing website client is not a substitute.
- User's existing private plugin ID: `plugins_6afcba53a45c8191b5c9511a991758aa`; extracted local source `D:\plugin\measuredfield-growth-workspace`. It contains four skills and no working remote `mcp.json`. Preserve its ID and existing skills. Do not create a duplicate.

## Safe repeatable diagnostic

From `mf_platform`:

```powershell
python -B scripts/mcp_live_release_preflight.py --json
python -B scripts/mcp_live_release_preflight.py --live --json
python -B scripts/mcp_private_connect_smoke.py --live --json
```

The first command does not touch the network. The second makes **three read-only HTTPS Render API GETs** to the single hard-coded service using the machine's `RENDER_API_KEY` (never displayed). It extracts only service identity, deploy status, env-key names and Git ancestry counts, and always returns `safe_to_enable_mcp: false` and `chatgpt_plugin_connected: false` until a separately verified OAuth/user flow exists. The last command probes four fixed public URLs and does not follow redirects, log tokens or access customer records.

At the first live check, preflight returned blockers:
`LIVE_AND_TESTED_SOURCE_DIFFER`, `PRODUCTION_BRANCH_DIVERGED` and `REQUIRED_MCP_CONFIGURATION_NOT_PRESENT`.
Do not treat an unconfigured or unlinked endpoint as healthy simply because the main application responds 200.

## Exact release order

1. **Reconcile Git without changing production.** Compare the two production-only commits with the main branch. Create a reviewable integration candidate, preserving both lines of history; do not overwrite the feature branch, force-push, or point Render at main blindly. Capture a reviewed SHA and its migration/asset delta. Stage on an isolated branch/test environment first.
2. **Auth0 resource registration (owner-approved).** Use the real public `https://measuredfield-staging-web.onrender.com/mcp` as the exact API resource identifier/audience, issue only `mf:read` for the first phase. Check real PKCE S256 issuance, expected `iss` and `aud`, resource parameter semantics and token validation; do not assume Auth0 automatically interprets `resource` as an audience.
3. **Separate OAuth clients.** Register a first-party confidential owner-link client callback `https://measuredfield-staging-web.onrender.com/app/connections/callback`, then a distinct ChatGPT OAuth client with ChatGPT's **actual displayed** callback URL. Since Auth0 discovery does not advertise a registration endpoint, prefer verified preregistration rather than invented DCR/CIMD. Owner authorization and user-session consent must happen interactively.
4. **Render configuration.** In correct MeasuredField service or approved shared env group, set `MF_MCP_OAUTH_ISSUER`, `MF_MCP_OAUTH_JWKS_URL`, `MF_MCP_RESOURCE_URL`, `MF_OIDC_LINK_CLIENT_ID`, `MF_OIDC_LINK_CLIENT_SECRET`, `MF_OIDC_LINK_AUTH_URL`, `MF_OIDC_LINK_TOKEN_URL`, and canonical `MF_CENSUS_API_KEY`. Confirm exact HTTPS hosts, distinct secrets, restricted runtime PostgreSQL role and applied migrations, without printing values.
5. **Disabled-default deploy and rollback.** Deploy a pinned reviewed release with `MF_ENABLE_REMOTE_MCP=false`, verify website and tenant isolation, backups, origin/Host proxy behavior and all core migrations. Only after the provider is proven and the operator approves the release, switch the flag to literal `true` through Render controlled deployment. Rollback is flag false plus redeploy/revoke compromised OAuth grants if necessary.
6. **MCP public security gate.** Require `/mcp` no-bearer 401 with `WWW-Authenticate: Bearer ... resource_metadata=`, protected-resource metadata 200 and exact resource/scope, strict foreign Host 421/404, hostile Origin 403, real OAuth tool listing and signed owner-token read only. Confirm ChatGPT request Origin before changing the SDK allowlist; never add wildcard origins.
7. **Attach the existing plugin only after successful endpoint verification.** Add a verified portable `mcp.json` with `type: streamable-http`, pinned HTTPS `/mcp` and the correct OAuth authentication settings. Update plugin `plugins_6afcba53a45c8191b5c9511a991758aa` through Plugin Creator. The owner explicitly installs/connects and approves OAuth, tests `get_connected_profile` plus a permitted business/Census read, cross-tenant denial and immediate Disconnect/revocation.
8. **Prescott's Best Construction project linkage is a separate ChatGPT UI action**, not an automatic side effect of plugin creation. Preserve its current project context. Do not grant the plugin blanket D-drive or email access; desktop editing requires independently authorized folder grants.

## Verification boundary

This story does not initiate paid resources, push Git commits to GitHub, alter the live Render service, register OAuth clients, disclose credential values, access customer data, connect a real ChatGPT account, or submit the plugin publicly. Manual approval and live account consent remain hard requirements for the production transition.

The preflight is intentionally diagnostic: even an entirely green metadata report **does not prove** OAuth browser consent, cross-tenant protection or plugin tool discovery until separate actual tests pass.
