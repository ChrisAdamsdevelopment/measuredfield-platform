# MF-050 — ChatGPT connection readiness and owner-first onboarding

**2026-11-17 (Arizona)** · Status: **local owner experience and offline diagnostics only; real ChatGPT plugin not installed, real OAuth not configured, no staging domain deployed**.

This story is the bridge from a secure MCP server and owner account-link code to an understandable, testable connection inside ChatGPT. It is NOT an authorization to deploy, purchase accounts, approve access to customer files or publicly submit a plugin.

## Four separate stages, never conflated

1. **Signed-in MeasuredField owner**: the workspace page is authenticated and tenant-aware. This proves only the normal MeasuredField session.
2. **Verified first-party owner identity**: a configured provider may offer a consented owner authorization-code + PKCE/OIDC flow. Account-link status means an OIDC issuer/subject is mapped to the current MeasuredField user. It does NOT prove that ChatGPT has installed a plugin or received a valid OAuth bearer. The Connect form appears only if server-side OIDC configuration is available. Existing per-user Disconnect remains active and stops future resource calls.
3. **ChatGPT installs and authorizes MeasuredField**: a separate actual HTTPS MCP resource, protected-resource discovery, authorization-server metadata and supported ChatGPT OAuth client registration (CIMD, DCR or approved predefined client) must exist. The user personally installs a custom MCP/Plugin in ChatGPT and approves scopes. No application code or synthetic state should mark this step completed.
4. **Read tool and revocation tested**: on a consented synthetic tenant, verify real in-ChatGPT read-only tools, deny a foreign tenant and confirm that Disconnect revokes subsequent calls. This demands live browser evidence and cannot be established by a local Python test.

A Windows folder/device companion is a SEPARATE permission domain. Never implicitly link files because an owner completed stage 2 or installed the plugin.

## Owner interface

The actual template at app/templates/app/mcp_connections.html is built on the existing authenticated app/base.html and loads scoped app/static/mcp_connections.css. It retains user-specific OAuth link listing, CSRF forms, explicit Read-only connect confirmation and Disconnect controls, while adding:

- A calm, readable owner explanation with no jargon required up front.
- Four ordered progress steps with statuses: signed-in session, identity linked/configured/missing, plugin install unverified, read-tool not tested.
- A genuine provider-linked status only when the current owner actually has an active link record. A configured IdP alone means setup available, NOT identity verified.
- An optional link to ChatGPT Plugins for navigation, explicitly stating that clicking it doesn't imply MeasuredField is listed.
- Separate computer access explanation and route to owner-controlled computer permissions.
- Responsive layout, keyboard/focus controls inherited from the app shell, status labels in plain language, reduced-motion styling, and a mobile table design.

**Stage truthfulness:** The owner UI does not claim it queried live staging or installed ChatGPT. Its stage 3 and stage 4 statuses remain NOT VERIFIED / NOT TESTED until a separate instrumented, approval-based deployment/testing feature is implemented. No secrets or account emails are presented in link tables.

## Offline CLI safety check

Use the previously implemented configuration auditor (updated tests in MF-050, no live external calls):

python -B scripts/chatgpt_connection_preflight.py
python -B scripts/chatgpt_connection_preflight.py --json

- Reports only environment **names** and safe issue codes, never config values, URLs, tokens, client IDs, email or passwords.
- Checks staging HTTPS, database host/domain settings, separate OAuth resource issuer/JWKS, exact /mcp resource path, first-party OIDC authorization/token endpoints and SDK version.
- Even with syntactically complete settings, returns ready_for_chatgpt=false because reachability, actual provider metadata, user consent, plugin install and real tool invocation are unverified.
- Current unconfigured local machine: 14 missing or invalid configuration entries + 3 warnings, expected exit code 2. Historical staging preflight: 7 blockers and 3 warnings. These are not proof of an application code test failure, and not credentials to enter automatically.

Official MCP 2.3.0 source and security conformance:
- scripts/mcp_public_host_conformance.py exercises the production MCP factory in-process under pinned SDK with valid Host 200, foreign Host 421, hostile Origin 403, unauthorized OAuth discovery 401 and insecure URL refusal.
- Existing MF-037, 039, 040, 041, 044, 045, 046, 047 security work requires independent peer review and exact acceptance evidence. MF-041, 045, 046, 047 peer reviewed DONE by Claude on November 17, 2026, but a real staging OAuth provider and public MCP transport have NOT been deployed.

## Focused and visual tests

python -B -m pytest -q tests/test_chatgpt_connection_preflight.py
python -B -m ruff check tests/test_chatgpt_connection_preflight.py scripts/chatgpt_connection_preflight.py

Synthetic Jinja page tests render the actual template with fake owner/link records and assert:
- No unconfigured identity Connect button or fake linked account claim.
- Configured IdP displays owner OIDC button, NOT installed-plugin status.
- A synthetic linked identity still leaves the ChatGPT install/read stages NOT VERIFIED and NOT TESTED.
- Provider names are HTML-escaped; Disconnect form and CSRF remain; a real connected status is not invented.

Actual browser QA runs on a synthetic HTML fixture, no business login and no provider request. Chrome normal --window-size=390 is NOT a real 390px viewport on Windows (it enforces minimum innerWidth=500, which caused an initially cropped screenshot). A subsequent offline Chrome DevTools session used Emulation.setDeviceMetricsOverride with **width=390, height=844, mobile=true**. It reported innerWidth=390, document scrollWidth=390 and no horizontal overflow; major container width 390, hero width 358, heading width 310. Review artifact: AGENT_BOARD/QA_MF050_CONNECTIONS_TRUE_MOBILE.png and QA_MF050_MOBILE_METRICS.json. The first cropped CLI screenshot is NOT evidence of a mobile layout bug. A future broad MF-058 design-system pass should optimize the whole workspace navigation on small phones; MF-050 limits styling to the connection workspace.

## Real private connection prerequisites — still NOT DONE

- Approved separately funded MeasuredField-only HTTPS staging host, app host and resource /mcp host, isolated PostgreSQL roles and backups, logging/privacy design.
- Approved OIDC/OAuth provider with an authorization-code + PKCE flow, validated issuer and signing keys, stable callback allowlist, user consent and proper resource-audience-bound tokens. Choose CIMD, DCR or pre-registered OAuth client as supported by actual provider; do not guess. Enable correct metadata discovery.
- Explicit owner login and browser consent, private custom MCP/Plugin install **inside ChatGPT**, read-only tool invocation against synthetic tenant, cross-tenant negative test and immediate disconnect revocation.
- Public plugin packaging with plugin.json / mcp.json and any required skills, assets, exact metadata, privacy/support pages, domain verification and OpenAI review. Submission is a separate request, not automatically accomplished by staging.
- Optional local computer connection is not an automatic side effect; only through approved, scoped, auditable device pairing and secure transport.

## Official documents checked November 17, 2026

https://developers.openai.com/plugins/deploy/connect-chatgpt
https://developers.openai.com/plugins/build/auth
https://developers.openai.com/plugins/deploy/submission
https://developers.openai.com/plugins/deploy/app-review
https://developers.openai.com/plugins/build/plugins
https://developers.openai.com/plugins/deploy/desktop-only

No external resources, OAuth tokens, paid APIs, customer records, production deployment, emails or domain changes were used by this story. An independent reviewer must verify the code, owner experience, tests and device permission language before MF-050 is marked DONE.
