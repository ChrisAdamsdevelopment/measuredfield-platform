# MF-048 — Portable MCP stdio security result assertions

**Status:** Implemented locally, awaiting independent review. No production/connector deployment.

The existing MF-046 real MCP subprocess test used the MCP Python 1.x result attribute `CallToolResult.isError`. MCP Python 2.3.0 exposes `is_error` instead. The test was therefore failing on an attribute error before it could verify that the old unpaired server correctly denied a read and proposed edit following account link revocation.

The test-only fix introduces a strict `_mcp_is_error` helper accepting either actual boolean result attribute. Missing, non-boolean, or contradictory flags raise an assertion; no false pass or default-to-success behavior. The same real subprocess security scenario continues to require success before linking, denial of file read and edit proposal after linking, absence of leaked file content and zero staged edits. A new pure test verifies both result spellings and malformed flags.

## Verified local tests — November 16, 2026 (Arizona)

- MCP 1.27.1 global Python: `tests/test_legacy_mcp_guard.py` — **18 passed, 0 skipped**.
- Official MCP 2.3.0 isolated environment: same test file — **18 passed, 0 skipped**.
- Ruff scoped to touched test file: **all checks passed**.
- No application, database, authorization or server runtime code changed in MF-048. MF-047 separately passed 804 full disposable real PostgreSQL-backed regression tests before this strictly test-only change.

The desktop companion and public OAuth resource server are not approved for remote deployment or general distribution. Independent review of MF-045–048 and the original MF-043 security follow-up is still required.
