# MF-051 — ChatGPT connection prerequisite auditor and real-world acceptance gate

**Status: OFFLINE READINESS IMPLEMENTED; LIVE CHATGPT CONNECTION NOT ESTABLISHED.** This work does not register a plugin, launch a tunnel, deploy a public service, enter an OAuth credential or connect customer data.

## Verified current blocker

Run from `mf_platform`:

```powershell
python -B scripts/chatgpt_connection_preflight.py
python -B scripts/chatgpt_connection_preflight.py --json
```

The first mode lists **only variable names and safe issue codes**. JSON provides machine-readable, source-safe stage statuses for a future owner status display. It deliberately reports `ready_for_chatgpt: false` even when all configuration values are syntactically valid, because real staging HTTPS, identity-provider discovery, owner browser consent, ChatGPT plugin installation and a tool call cannot be proven from a configuration file.

On the current development machine the report identifies **14 distinct missing/malformed configuration entries** (including missing staging database/host/secret and resource/OIDC issuer settings) and **3 warnings**. The older general staging preflight separately reports seven core deployment blockers. No secrets are printed or written to a report.

The current local Python MCP SDK is 1.27.1, whereas production requirements pin **mcp 2.3.0**. Isolated MCP 2.3.0 tests under `D:\DevCache\mf-mcp-sdk-v2\Scripts\python.exe` passed previously, but the global CLI test environment should not be mistaken for the production runtime.

## What is implemented, and what is not

- The read-only MeasuredField MCP endpoint is coded to expose scoped owner/tenant information. A real MCP SDK 2.3.0 protocol test with accepted **synthetic** token has passed. OAuth protected-resource metadata, 401 bearer challenges, exact public Host allowance and hostile Origin rejection are locally tested. Real production endpoint: **not deployed**.
- The owner OIDC account enrollment flow and Disconnect controls are coded with per-user session, CSRF, PKCE/nonce, signed ID-token checks, revocation and issuer pinning. Real identity provider configured and user browser sign-in: **not established**.
- The Windows folder companion has owner-authorized local grants, file edit proposals and per-call revocation checks. A production installer, trust-pin and real ChatGPT-to-PC Secure MCP Tunnel connection: **not established**.
- Actual ChatGPT Plugin installation, read-only tool invocation and verified immediate revocation after Disconnect: **not established**.

This separation is mandatory. Successful development middleware tests do not prove a real user's ChatGPT account has connected.

## Exact path to a real test

1. **Choose and approve staging resources.** Use a separate MeasuredField hosting account (not Prescott's Best Construction), approved spending limit, application and connector HTTPS hostnames, Postgres owner/runtime roles and backup plan. Run `scripts/staging_preflight.py` before provisioning. Never put keys in the repository or chat.
2. **Configure a real OIDC/OAuth identity provider.** Pin `MF_MCP_OAUTH_ISSUER`, JWKS and `MF_MCP_RESOURCE_URL` to real HTTPS domains, register a separate first-party owner PKCE client for `/app/connections/callback` and arrange OAuth client registration for ChatGPT using supported mechanisms (pre-registered client, DCR or CIMD as appropriate). Distinguish owner identity linking from ChatGPT authorization to the MCP server.
3. **Deploy to HTTPS staging and test with fictional data only.** Confirm authenticated MCP initialization, exact Host through reverse proxy, TLS, 401 discovery, registered resource scopes, read-only tools, tenant isolation, absence of write tools and revocation from a second session. Do not deploy unreviewed MF-037–048 code to public customer traffic.
4. **Create the custom plugin in a supported ChatGPT workspace.** On ChatGPT web, open **Plugins → + → Add custom MCP server**, enter the public `https://.../mcp` URL (or an explicitly owner-authorized Secure MCP Tunnel), configure authentication and confirm the risk dialog. Select **Create as a plugin**, inspect the discovered tools and install privately. Workspace policy/plan permissions apply; full MCP support is currently documented for Business and Enterprise/Edu, with more limited Pro capabilities. Alternatively, Plugin Creator can package a publishable plugin for the directory or Sites.
5. **Complete live account authorization interactively.** The owner signs into the actual identity provider, approves the requested scopes, confirms the link on the MeasuredField Connections page, and runs a read-only ChatGPT tool for a synthetic tenant. Prove it reads only that owner's sample business and refuses the other tenant's information.
6. **Verify revocation and audit.** Disconnect the ChatGPT identity link from MeasuredField; repeat the previously successful tool call and require a denial. Test token expiry, lost IdP, wrong audience, wrong scope, changed Origin/Host and stale sessions. Do not call this live unless the actual full round trip succeeds.
7. **Keep computer editing separate.** Only after independent review of the companion and a Secure MCP Tunnel should the owner connect a selected folder and review a proposed edit. Deletion, production publishing, email sending, paid searches and arbitrary shell actions require their own scoped authorization; no blanket autonomous access.

## A better product-distribution approach

Avoid requiring every small-business owner to understand MCP or run a ChatGPT Business workspace. Build a distributable MeasuredField Plugin with plain-language tools such as **Show my top lead opportunities**, **Compare my local visibility snapshots**, **Draft my next steps** and **Prepare a proposed website edit**. The MeasuredField app owns identity, first-party records, consent and approvals; ChatGPT is a conversation/action interface to those tools. Plugin Creator and official submission guidelines support packaged public plugins, but app submission, workspace entitlements and user authorization remain separate explicit processes.

Current references (verified November 2026):
- https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt
- https://developers.openai.com/plugins/quickstart
- https://developers.openai.com/plugins/build/app-quickstart
- https://developers.openai.com/plugins/deploy/submission
- https://developers.openai.com/plugins/build/auth
- https://help.openai.com/en/articles/20001547-hosting-a-plugin-with-chatgpt-sites

## Security assurances and test limits

The auditor invokes no external URLs, does not load customer data, and returns no environment *values*: never hostnames, tokens, client IDs, passwords, email addresses or database URI. It uses the existing staging and MCP strict URL checks and distinguishes configured, externally verified and fully connected states. Synthetic fixture tests include missing configurations, HTTP resource refusal, wrong issuer host, absent OIDC path, credential redaction and CLI non-zero exit even with syntactically valid staging settings.

Use `python -B -m pytest -q tests/test_chatgpt_connection_preflight.py`, `python -B -m ruff check scripts/chatgpt_connection_preflight.py tests/test_chatgpt_connection_preflight.py`, and `python -B scripts/secret_scan.py`.

Independent security review remains required before production use.

## Verified local development checkpoint (November 16, 2026, Arizona)

- Running `python -B scripts/chatgpt_connection_preflight.py` on the authorized machine produced `missing_configuration`, **14 safe missing/invalid configuration field names**, and **3 warnings**. No credentials, hostnames, customer data or provider requests appeared in output. It correctly states the real ChatGPT login has **not** happened.
- Dedicated no-secret CLI and OAuth/hostname-validation test suite: **15 passed** on global MCP 1.x Python, **15 passed** again in isolated official MCP 2.3.0 Python, zero skips.
- Ruff on touched script/tests: passed.
- Full disposable PostgreSQL regression suite after this new independent diagnostic: **841 passed, zero skipped/errors/failures**, `CI_GATE_PASS`; the test database container was stopped and removed.
- This commit is only a readiness auditor and its security/source tests. Pending MF-041/MF-045–048 independent reviews, external deployment, identity-provider registration and actual ChatGPT browser installation remain uncompleted.
