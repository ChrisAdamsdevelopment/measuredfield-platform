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

## Independent review correction and follow-up — November 17, 2026

Claude's first independent MF-043 review validated the paired server's live device check, but found a **separate old unpaired MCP entrypoint** (`app.local_connect.mcp_server`) that could still read previously granted files after account disconnect. That issue was **implemented as a separate MF-046 repair in commit `fd85ff1`**, which blocks legacy unpaired startup and pre/post file operations whenever account state or a DPAPI credential is present. Its dedicated `tests/test_legacy_mcp_guard.py` includes a real separate stdio process losing access when linked. **MF-046 remains pending independent approval.** Do not mark MF-043 fully release-ready while that dependent bypass review remains outstanding.

MF-043 now includes `test_running_paired_process_refuses_after_owner_disconnect`, a real isolated child Python process with a stored pairing token. The parent simulates an account transition from never-linked to revoked while that process keeps its `BoundSession`; the next read must be denied, and the parent must verify that all local pairings were revoked. No real account, provider, HTTPS request or production file is involved. This adds executable cross-process evidence beyond new instances of the same Python class.

The local-only pilot continues to have an explicit threat-model limitation: a same-user attacker who can delete both the account metadata and DPAPI credential can restore never-linked legacy behavior. Before any remote tunnel or third-party installation, require a signed installer/immutable device trust anchor and removal or hardening of that fallback. A clean local test alone cannot certify a publicly distributed computer connector.

Independent security review remains mandatory before public release.
