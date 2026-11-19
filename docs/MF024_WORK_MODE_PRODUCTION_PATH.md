# MF-024 — Work-mode production path

The MeasuredField production account can link a named computer with one of Read only,
Suggest changes or Work in approved projects. Work never implies whole-drive access,
shell access, deployment rights or access to other customer accounts. The desktop
owner separately selects readwrite project folders and approves a time-limited
local pairing (eight hours maximum).

Within an active Work-mode local MCP pairing, write_project_file and
create_project_directory REALLY change the selected project on disk. Replacements
require a correct old SHA-256; new files use exclusive-create; existing-file
replacements use fsynced temporary files and atomic replacement. Each invocation
rechecks cloud account status, local pairing and folder permissions. Linked/reparse
paths, traversal, protected credential paths and oversized or binary content are denied.
Mutations produce SHA-256 receipts and local activity audit entries.

The 0017 migration extends the stored connected-device permission CHECK to Work.
Old read/suggest records stay unchanged and cannot gain direct write permission.
The downgrade refuses to erase a currently existing Work-mode state.

## Test and release boundary

- Canonical production code, not a separate demo or disposable development app.
- Local tests exercise real file create/replace, folder creation, revocation,
  stale baseline conflicts, protected paths and standing Work-mode authorization.
- Database-backed tests cover link code, confirmation, Work permission, metadata,
  RLS and revocation.
- Still required for complete production operation: cloud ChatGPT OAuth MCP tool
  to authenticated desktop request delivery, active desktop polling/claim agent,
  encrypted/idempotent payloads and signed work receipts; then a real user end-to-end
  invocation. CLI/UI here do not automatically enable an Internet tunnel.
- Execution and deployment remain separate capabilities and require dedicated
  owner permission and tests before they can be advertised as operational.

No API keys belong to this change. Keys remain in restricted local D drive
credential storage before entering the target Render service. Do not commit
keys, access tokens, provider responses or customer data.

Security review follow-up: connectivity loss or an unverified linked account fails closed and permanently revokes active local pairings. The owner must establish a fresh temporary pairing after connectivity is restored; no silently resumed Work session. Suggest and Work checkboxes are mutually exclusive. Shared Windows reparse-point detection is used across local token storage, account state, and DPAPI secrets.
