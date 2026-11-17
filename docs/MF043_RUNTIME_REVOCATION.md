# MF-043 — check cloud device authority on every private local MCP tool call

Status: **local-only private pilot; pending independent review.** No always-on daemon, remote tunnel, browser connector, live OAuth registration, provider credentials, production deployment or filesystem changes were enabled.

## Background and security issue

Claude's independent MF-034 review confirmed the look-alike workspace pairing relay was fixed. A residual problem remained: the Tk configuration window used to poll the workspace for device revocation, but a separately running paired stdio MCP server consulted only its own 15-minute-to-24-hour local pairing token. Closing the Tk window could leave that process able to read files after the remote owner clicked Disconnect.

MF-043 applies account authorization per MCP file tool invocation, not only in the GUI. A persistent process cannot continue using previously approved local access just because its Windows window is closed.

## Behavior

- Each BoundSession method (projects, browse, read, propose, pending, activity) still checks its local SQLite pairing, selected folder grant and expiry on every action.
- Additionally AccountRuntimeGuard reads the same state JSON and Windows DPAPI-protected device credential used by the desktop owner application and, for an active linked device, makes a fresh HTTPS /api/device/v1/status request via pinned installation origin. It requires the exact device ID, active status and appropriate read/suggest permission. No account access is cached across calls.
- Revoked, different device, pending, offline, read-only when suggesting edits, missing/corrupt state, incompatible pinned origin, or missing credential: deny current call and revoke all local pairings. No file content or edit proposal leaves the machine.
- Never-linked legacy local-only pilot retains approved, expiring scoped local pairings when there is no connection metadata and no stored DPAPI credential. It has no remote route.
- No listener, tunnel, agent control API, shell or automatic pairing API.

## Limitations

- **Revocation takes effect at the next MCP tool invocation**, subject to the device HTTP timeout, even after the Tk GUI closes. It does not proactively terminate an idle stdio process or interrupt file requests already in progress.
- An outage fails closed and revokes existing local pairings. The owner must deliberately re-pair after restoration. This prioritizes security over availability.
- The pinned HTTPS workspace address still originates in an environment variable. **Before public distribution**, move the address into a signed build or administrator-managed configuration. This change does NOT protect against a person with access to modify the entire Windows OS, app executable and environment.
- No ChatGPT OAuth identity, authenticated remote tunnel, background service or cloud relay is enabled. MF-024 remains a separate blocked production launch dependency.
- Local grant and owner approval are separately enforced; this component never approves file writes.

## Implementation and tests

- app/account_link/runtime_guard.py: fail-closed per-call account status.
- app/paired_connect/registry.py: BoundSession._verify checks paired local grants and server status before every model-callable file tool.
- tests/test_account_runtime_guard.py: synthetic account and file fixture, offline/revocation/read-only downgrade, mismatched device, corrupt JSON, forged origin, lost pin, TOCTOU local disconnect and legacy pilot.
- All tests are synthetic, no paid API, no production credentials or customer data touched.

Independent security review remains mandatory before public release.
