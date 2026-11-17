# MF-034 — Account-linked Windows Connected Workspace client

Status: local first-party pilot implementation. No public tunnel, ChatGPT connector, production deployment, external billing or customer-file access activated by this story.

## Owner journey

1. Sign in to the private MeasuredField workspace and open Computers (/app/computers). Create a 10-minute one-time pairing code, choose a name and either Read only or Suggest changes.
2. On the Windows computer, open Connected Workspace and choose Connect this computer. The workspace address is **pre-installed and read-only**; enter the computer name and one-time code. There is no free-form HTTPS address to type. A signed MeasuredField installation must pin the real HTTPS workspace with MEASUREDFIELD_WORKSPACE_URL before pairing can be enabled. Without it, the Connect action stays disabled. Only explicitly enabled local developer mode accepts a loopback URL (localhost/127.0.0.1/::1) on the same computer.
3. The computer displays a six-digit number. Type that number into the authenticated MeasuredField Computers page. The number is never stored to disk.
4. Choose Check confirmation, or leave the window open to poll. Connected and the business name appear only after the server confirms the device status.
5. Connect local folders separately. A Read-only account cannot grant suggestion permission through the window, approve a local proposed edit, or create a suggested-edit pairing. Previously created suggested-edit sessions are revoked when account status becomes pending or read only.
6. Choose Disconnect this computer or use Disconnect now in the workspace. On revocation/401, the client clears its encrypted credential and revokes ALL active local trusted MCP pairings. The window polls every 15 seconds while open. A temporarily unverified server status also fails closed for local pairings.

## Files and behavior

- app/account_link/client.py: typed HTTP client for the MF-032 pair/status/heartbeat/rotate/disconnect/activity routes. Pins the exact HTTPS origin from trusted installation configuration before any network exchange, rejecting look-alikes and changed saved state; no free-form remote host selection. Loopback hosts only under an explicit MEASUREDFIELD_DEVELOPER_MODE=1 opt-in with no pinned remote origin. Rejects credentials embedded in URLs and insecure non-loopback HTTP. Never displays raw HTTP errors or device credentials in dataclass representations.
- app/account_link/secret_store.py: Windows CryptProtectData/CryptUnprotectData via DPAPI, with UI forbidden. The device credential lives only in a user-scoped encrypted file. No plaintext fallback. Non-Windows callers fail closed.
- app/account_link/controller.py: owner-only state machine and non-secret state file, local pairing revocation, and Read only enforcement.
- app/local_connect_ui/account_panel.py and window.py: Tk owner account tab, confirmation number, status, explicit disconnect. Credential never appears in Tk state.
- tests/test_account_link.py: adversarial synthetic network tests, real-device-router integration on disposable PostgreSQL and Windows DPAPI round trip.

## Limits that must remain visible

This pairs a first-party desktop window to a MeasuredField account. It does NOT yet connect ChatGPT or grant internet access to a computer. The device credential is a bearer secret rather than a key bound to a device. The local folder connector and locally paired MCP sessions are a separate permission system. Any future tunnel needs an authenticated link between account identity, local folder grants and every remote action.

Server status is polled while this window remains open. Closing the window suspends status polling; therefore this is not a permanent cross-process remote kill switch for an independently launched MCP server. Such a server still has its own short expiry and owner revoke controls. A follow-on design is required before claiming continuously enforced remote revocation.

No customer files, production secrets, payment transactions, live messages or deployed websites are changed.

## Verification

Focused tests: python -B -m pytest -q tests/test_account_link.py

Full disposable real PostgreSQL: python -B scripts/run_disposable_pg_audit.py --full-suite

Run the full suite and tracked-file secret scan under the shared integration gate, commit only these scoped files, and submit for independent review.


## Independent Argon security-review remediation (November 16, 2026)

Argon High's static review of MF-034 commit c40b052 requested changes. The follow-on fixes address:

- A corrupted or unreadable Windows DPAPI credential now fails closed during status refresh, revokes active local sessions, and clears the active/suggest permissions instead of leaving the old state visible.
- The in-memory state changes to revoked before fallible encrypted-file deletion. Cleanup errors are surfaced without restoring permissions.
- Corrupted/missing account state with an existing device credential is classified as unverified, never as a fresh unlinked account. Cached active permissions are not trusted on app restart; pre-existing local sessions are revoked until a fresh server check.
- Pending, unverified, and revoked account states prohibit creating even read-only local MCP pairings. Each account-linked pairing checks the server at creation time.
- The Connected Workspace window triggers an early status refresh, updates the trusted-session panel when account state changes, and rechecks server permissions immediately before applying local file changes or granting suggested edits, including after owner confirmation dialogs.
- A failure while saving a newly linked device's nonsecret local state triggers a safe rollback, credential cleanup and a best-effort server disconnect instead of reporting a connected state.
- New synthetic tests exercise corrupted credentials and state, deletion failures, rejected local pairing, stale session startup, changes of device identity, redirect rejection, and state-save failure.

Limits remain: a separately launched local MCP process is not continuously controlled by the account-status polling of a closed Tk owner window. A production-grade remote connector must bind real file operations to short-lived device/account authorization or an independent, enforced revocation check. No external tunnel is activated here.
