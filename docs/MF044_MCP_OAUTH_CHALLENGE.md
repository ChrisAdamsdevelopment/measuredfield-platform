# MF-044 — OAuth 401 discovery contract gate

**Status:** Local-only, independent review required. No ChatGPT connector has been activated.

This tiny engineering story ensures that the unauthorized HTTP response from the *actual* official MCP Python SDK points a ChatGPT client to the correct HTTPS protected-resource metadata URL. It complements MF-037 and MF-039 without changing any production handlers or initiating a live provider connection.

## Scope

- `scripts/mcp_oauth_http_probe.py` runs a real in-process MCP 2.3.0 ASGI app under Starlette TestClient.
- Synthetic resource: `https://connector.synthetic.test/mcp`; synthetic issuer: `https://login.synthetic.test`.
- Token verifier rejects **all** tokens in memory; no JWKS HTTP, secrets, user identity, customer data, file access, or external API calls.
- The guard checks `/.well-known/oauth-protected-resource/mcp` returns exact resource, authorization servers and read scope.
- Both missing Authorization and deliberately malformed Bearer must return HTTP 401, with a precise `WWW-Authenticate: Bearer resource_metadata="https://connector.synthetic.test/.well-known/oauth-protected-resource/mcp"` challenge. A wrong host, non-HTTPS location, absent challenge or silently accepted bearer fails the probe.

## Execution

Run in an **isolated** Python environment with `mcp==2.3.0` and MeasuredField project dependencies installed. Do not downgrade or replace the system-wide MCP 1.x. From the canonical repository directory:

```powershell
python -B scripts/mcp_oauth_http_probe.py
```

An SDK version other than 2.3.0 is an explicit **failure**, not a skipped test. The test only calls ASGI TestClient in process.

## Release limits

Passing is necessary for correct HTTP linking discovery, not sufficient for real ChatGPT OAuth login. IdP support for PKCE S256 and client registration, exact token resource audience, staging TLS and ChatGPT resource indicator, account-link revocation, independent security approval and owner deployment authorization remain outstanding. Tool-level `mcp/www_authenticate` behavior may also require real staging verification.

## Local verification — November 16, 2026 (Arizona)

- Isolated environment: `D:\DevCache\mf-mcp-sdk-v2\Scripts\python.exe`, official `mcp==2.3.0`; global Python remains on MCP 1.27.1.
- In-process probe: `MF044_MALFORMED_CHALLENGE_REJECTION_PASS`, `MF044_DISCOVERY_PASS`, both `MF044_401_CHALLENGE_PASS` cases and final `MF044_OAUTH_HTTP_CONFORMANCE_PASS`.
- Existing connector-focused tests: **38 passed** (`tests/test_mcp_plugin.py`, `tests/test_mcp_plugin_metadata.py`).
- `ruff check scripts/mcp_oauth_http_probe.py`: **All checks passed**.
- `git diff --cached --check`: clean.
- `scripts/secret_scan.py` after staging: **SECRET_SCAN_PASS**, 264 tracked files.
- No database or production code modified; full disposable PostgreSQL suite not rerun for this isolated no-network probe.
