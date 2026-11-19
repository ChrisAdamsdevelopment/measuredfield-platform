# MF-039 — ChatGPT OAuth tool metadata and stable connected-account profile

Status: implemented in the canonical MeasuredField Git main checkout, **not deployed or connected to ChatGPT**. This slice is a security/compatibility improvement atop the committed MF-037 read-only MCP resource server, which remains pending independent approval.

## Why this changed

A local critical review of MF-037 against the current OpenAI Plugins authentication and MCP server documentation (November 2026) found that the MCP v2 resource server was advertising OAuth only on the HTTP resource boundary. It lacked *per-tool* security metadata, and ChatGPT could not reliably distinguish two accounts because the server did not have an authenticated profile tool.

Current official references:
- https://developers.openai.com/plugins/build/auth — per-tool `_meta.securitySchemes`, OAuth linking behavior, and `mcp/www_authenticate` for tool-level auth challenges;
- https://developers.openai.com/plugins/build/mcp-server — `_meta["openai/profile"]: true` for authenticated multi-account profile; read-only tool annotations.

## Implementation

Only `app/mcp_plugin/server.py` was modified. `oauth_tool_metadata(scope, profile=False)` produces `{"securitySchemes":[{"type":"oauth2","scopes":["mf:read"]}]}` and only the special profile tool receives `"openai/profile":true`. Every tool has its own metadata; none advertise unauthenticated access or `mf:write`.

The read-only `get_connected_profile()` tool takes **zero** model-supplied parameters. It checks the bearer signature, issuer, audience, scope, client ID, issued-at time and live verified identity link; its opaque ID is the already-persisted MeasuredField `User.id` UUID, which remains stable across tokens and logins. It does not reveal emails, links, raw OAuth subjects, payment details or cross-tenant data.

Existing read-only tools remain: list_my_businesses, get_business_overview, list_inquiries, get_inquiry, preview_reply (draft only; never saved or sent). The tools recheck the actual token and business access; `get_access_token()` alone is not considered proof of authorization in a direct/in-memory invocation.

## Verification

- `python -B -m pytest -q tests/test_mcp_plugin_metadata.py tests/test_mcp_plugin.py`: pure tests verify the explicit metadata constructor and all six actual MCP decorators, invalid scopes, account profile derivation and the existing JWT rejection cases.
- Isolated official Python MCP SDK **2.3.0** on D: verifies every registered Tool's metadata and read-only flags, rejects unauthenticated direct tool calls, returns 200 for `/.well-known/oauth-protected-resource/mcp`, and returns **401 with WWW-Authenticate** for unauthenticated `POST /mcp`. The reference script is `AGENT_BOARD/MF039_SDK_VERIFY.py`; it requires no user identity or live network provider.
- The full disposable PostgreSQL application regression suite and staged/tracked secret scans are required before merging.

## Limitations and remaining release gates

The official SDK handles the HTTP 401 discovery challenge; some ChatGPT tool-level OAuth linking flows additionally expect tool-result `_meta["mcp/www_authenticate"]`. This slice does **not** claim a real ChatGPT linking session, which must be tested on an approved HTTPS staging endpoint with provider-issued tokens. If ChatGPT fails to display authorization UI, inspect the tool-level challenge and SDK capabilities before release.

A real OAuth 2.1 / OIDC provider with PKCE S256 and supported ChatGPT client registration is not configured yet. An owner-verified, consented enrollment/disconnect journey and real provider discovery remain incomplete. Do not register a live plugin, give it production customer data, send messages, access local drives or perform paid AI visibility calls based on this isolated test.

## Review accountability

GPT-6 authored and self-checked this change because Argon and Claude were usage-limited. A self-check is **not independent approval** under the MeasuredField agent board. Keep independent-review status pending until another agent completes it.
