# MF-046 — Block legacy unpaired stdio MCP access after account linking

**Status:** Local-only code change, pending independent review. No remote connector, tunnel, or production deployment.

## Finding

Claude's independent MF-043 security review established that the newer paired MCP server consulted the live device status on every file call, but the older `app.local_connect.mcp_server` still called `LocalConnect` directly. It could therefore serve previously granted files after the owner clicked Disconnect, while offline, or when the account permission changed to read-only. It was separately discoverable via `python -m app.local_connect.mcp_server` and `python -m app.local_connect.cli server`.

## Fix

- New `AccountRuntimeGuard.require_never_linked_local_pilot()` permits use only when **neither** an `account_connection.json` record **nor** a DPAPI credential exists. Merely having an account state, even disconnected, blocks the older unpaired protocol; active linked devices must use the owner-approved *paired* launcher with full remote status checks.
- The legacy server refuses startup before initializing stdio when the machine has link metadata. Its six model-callable tools independently enforce the never-linked check **before and after** their LocalConnect operation. If the computer is linked while an operation is in progress, the result is not returned.
- The local CLI help and original user guide explain the stricter rule.
- Never-linked, owner-approved per-folder grants and human-reviewed proposed edits remain available for the private pilot.
- The change does not create a tunnel, alter network rules, issue credentials, automatically approve edits, or call an identity provider.

## Security boundary and limitations

This change prevents accidental old-server bypass on an honest owner's computer after a normal account connect/disconnect. It cannot protect against a same-user attacker who can erase **both** the local account JSON and DPAPI credential, modify the executable or run a different local file server; real commercial remote connectivity still requires a signed, controlled installer, protected launch path, authenticated tunnels, and server-side authorization. The pre/post tool check does not interrupt an in-flight physical file read, but does refuse to return its content if link state changes before the return check.

## Synthetic tests

`tests/test_legacy_mcp_guard.py` exercises the six old MCP tools, all link states, corrupted state, orphan credential, server startup and CLI refusal, pre/post read race, preservation of never-linked grants, and a separately running Python process that is refused after its owner creates account-link state. No real device or customer files are accessed.

Run:

```powershell
python -B -m pytest -q tests/test_legacy_mcp_guard.py tests/test_account_runtime_guard.py tests/test_local_connect.py
python -B scripts/run_disposable_pg_audit.py --full-suite
```

Independent security review remains required for MF-046 and the original MF-043 finding; nothing has been approved for remote computer access.

## Verified local results — November 16, 2026 (Arizona)

- Local legacy-server tests: **17 passed**, including a real separate MCP stdio subprocess that loses read/propose access after account link, and refusal to start when already linked.
- Combined targeted unit tests before added stdio checks: **72 passed**; the full run includes all new tests.
- Full real disposable PostgreSQL suite: **799 passed**, zero errors, failures or skips; test container stopped and removed.
- Ruff checks for all touched Python files: **passed**.
- **Separate SDK compatibility issue:** the isolated, pinned MCP 2.3.0 environment cannot import `mcp.server.fastmcp` in either old stdio server. That is distinct from this security gate and is tracked as MF-047. Do not claim the old stdio launchers work under MCP 2.3.0 until MF-047 is fixed and retested.
