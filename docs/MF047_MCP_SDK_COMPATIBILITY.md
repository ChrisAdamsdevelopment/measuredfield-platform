# MF-047 — Private stdio MCP SDK v1/v2 compatibility

**Status:** Local-only implementation, independent review pending; no remote computer connector deployed.

## Root cause

The canonical MeasuredField requirements pin official `mcp==2.3.0` for its public OAuth-protected server, but two older private stdio processes imported `mcp.server.fastmcp.FastMCP`. That module deliberately raises `ModuleNotFoundError` under 2.3.0. The older global Windows installation used MCP 1.27.1, hiding the mismatch.

## Change

- `app/local_connect/mcp_server.py` and `app/paired_connect/server.py` now import `MCPServer` from `mcp.server.mcpserver` when the official SDK v2 module is available, falling back to the existing FastMCP v1 import only for previous local installations.
- No change to pairing credentials, folder grants, cloud device checks, transport listener, tool names, approval policy or linked-computer lockout.
- `tests/test_mcp_stdio_sdk_compat.py` exercises **actual separate MCP stdio subprocesses** with official client protocol on both SDK major versions, not just source assertions: never-linked local file read, account-link revocation, paired token authorization, pair revoke, empty token denial, and account-state fail-closed.
- No provider call, tunnel, OAuth registration, user data or production deployment.

## Verified local tests

- Isolated `D:\DevCache\mf-mcp-sdk-v2\Scripts\python.exe` with official MCP **2.3.0**: **5 new stdio protocol compatibility tests passed**, zero skips/failures.
- Global Python MCP **1.27.1**: **32 compatible and paired tests passed**, zero skips/failures.
- Touched-file Ruff: passed.
- A separate pre-existing MF-046 test `test_actual_separate_stdio_mcp_process_denies_after_link` uses the older test client's result spelling `isError`. MCP 2.x exposes `is_error`; its cross-version test harness requires an independent fixture change, while the new MF-047 tests deliberately accept either spelling. This is a test compatibility issue, not an authorization success. Do not claim the entire historical test suite runs unchanged under MCP 2.3.0.
- Full disposable PostgreSQL suite, secret scan and staging release are separate checks.

## Deployment gate

The private stdio server is still local-only; local pairing does not authenticate a remote ChatGPT identity. MF-043/MF-046 independent security approval, installer-bound trusted origin, approved OAuth/tunnel/tenant boundary and deliberate owner consent remain required before remote computer access.

## Complete regression outcome — November 16, 2026 (Arizona)

- Full disposable real PostgreSQL regression suite after SDK import changes and five new stdio protocol tests: **804 passed**, zero skipped, errors or failures in 250.70 seconds.
- `CI_GATE_PASS`; ephemeral database container stopped and removed.
- Project-specific changed-file Ruff passed, scoped source secret scan passed before staging.
- This proves local compatibility, not a real public ChatGPT connector or Windows installer release.
