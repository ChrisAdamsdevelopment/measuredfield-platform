# MF-034 — Account-linked Windows Connected Workspace client

Status: local first-party pilot implementation. No public tunnel, ChatGPT connector, production deployment, external billing or customer-file access activated by this story.

## Owner journey

1. Sign in to the private MeasuredField workspace and open Computers (/app/computers). Create a 10-minute one-time pairing code, choose a name and either Read only or Suggest changes.
2. On the Windows computer, open Connected Workspace and choose Connect this computer. Enter the exact HTTPS workspace origin, computer name and one-time code. HTTP is allowed only for loopback development hosts.
3. The computer displays a six-digit number. Type that number into the authenticated MeasuredField Computers page. The number is never stored to disk.
4. Choose Check confirmation, or leave the window open to poll. Connected and the business name appear only after the server confirms the device status.
5. Connect local folders separately. A Read-only account cannot grant suggestion permission through the window, approve a local proposed edit, or create a suggested-edit pairing. Previously created suggested-edit sessions are revoked when account status becomes pending or read only.
6. Choose Disconnect this computer or use Disconnect now in the workspace. On revocation/401, the client clears its encrypted credential and revokes ALL active local trusted MCP pairings. The window polls every 15 seconds while open. A temporarily unverified server status also fails closed for local pairings.

## Files and behavior

- app/account_link/client.py: typed HTTP client for the MF-032 pair/status/heartbeat/rotate/disconnect/activity routes. Rejects credentials embedded in URLs and insecure non-loopback HTTP. Never displays raw HTTP errors.
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
