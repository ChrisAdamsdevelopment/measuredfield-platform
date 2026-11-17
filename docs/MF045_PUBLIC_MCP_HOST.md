# MF-045 — Public MCP Host Security

**Status:** Local engineering fix, awaiting independent review. No deploy or actual account linking.

Claude's independent MF-044 review found that MCP SDK 2.3.0 defaults its streamable HTTP Host allowance to loopback when `host` / `transport_security` are omitted. A request with an accepted bearer therefore returned **421** on the configured public domain, despite unauthenticated 401 discovery working normally.

The production `create_app()` factory now derives the exact HTTPS hostname from `McpPluginSettings.from_env().resource_url`, which performs strict scheme/host/port/URL checks. The SDK's `TransportSecuritySettings` explicitly keeps DNS rebinding protection **enabled**, with an allowlist limited to the configured public host (and its explicit standard HTTPS `:443` equivalent); permitted Origin values are precisely the same HTTPS origin and its explicit `:443` equivalent. No wildcard host, proxy-derived authorization, disabled guard, development server fallback or live provider access was introduced.

## Tests

`scripts/mcp_public_host_conformance.py` invokes the **real production app factory** under the real SDK 2.3.0, using an accepted synthetic token verifier solely to reach HTTP transport checks. Its request/response behavior is not mocked. It tests valid Host 200, standard HTTPS port, valid Origin, foreign/forged Host 421, hostile Origin 403, unauthenticated challenge 401 with pinned resource metadata, correct discovery response and factory failure for HTTP resource URL.

Run in isolated SDK 2.3.0:
`D:\DevCache\mf-mcp-sdk-v2\Scripts\python.exe -B scripts/mcp_public_host_conformance.py`.

## Staging and release gate

This is a local security-conformance change only. Before enabling a public ChatGPT connector, verify Render's HTTPS forwarding preserves the actual resource Host, a configured identity provider issues resource-audience-bound tokens, and the staging endpoint is bound to the approved domain. If the host is rewritten by a proxy, fix the trusted proxy routing rather than accepting wildcard Hosts. SDK Origin checks may require a separate explicitly approved ChatGPT Origin after a real staging integration; do not add it speculatively.

MF-037, MF-039 and MF-041 still require independent review, and MF-024 remote file connector remains gated. No DNS, environment secrets, production code deployment, paid provider calls or user data were touched.

## Verified local results — November 16, 2026 (Arizona)

- Official isolated MCP 2.3.0 app factory probe: `MF045_PUBLIC_HOST_CONFORMANCE_PASS` (200 valid host, 421 foreign Host, 403 foreign Origin, 401 unauthenticated, protected-resource discovery intact, insecure resource rejected).
- Existing MCP connector focused tests: **38 passed**.
- Touched-file Ruff: **all checks passed**.
- Full disposable PostgreSQL suite: **782 passed**, zero errors/failures/skips in 220.49 seconds; `CI_GATE_PASS`, database container stopped and removed.
- Tracked secret scan: `SECRET_SCAN_PASS`, 264 tracked files before staging.
- Change is local only and remains subject to independent security review and real HTTPS staging validation.
