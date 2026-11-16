# MeasuredField Connected Workspace — Local Computer Companion (MF-027)

Status: working local development slice, not a commercially released or ChatGPT-connected plugin. Owner approval is required before a proposed edit changes a project file.

## Why this is central to the business

An owner should be able to say: Connect my computer, select my project, make the requested changes, let me review the result, and only apply it if I approve. The project should not require learning Git, PowerShell, hosting, or software packaging first.

MF-027 establishes the local file permission and review foundation independently of the unfinished cloud OAuth work in MF-013 and MF-024.

## Functional capabilities

- Owner-selected folders only. Grants default to read-only; write-proposal capability is a separate explicit opt-in.
- Per-owner local SQLite under Windows Local AppData: MeasuredField\ComputerConnect. Synthetic tests may override with MEASUREDFIELD_LOCAL_CONNECT_HOME.
- Private Python MCP stdio server (no public port), using the official installed Python MCP SDK.
- AI-callable tools: list_granted_projects, browse_project, read_project_file, propose_project_edit, list_pending_edits, recent_connection_activity.
- NO AI-callable grant, owner approval, shell execution, direct file write or deletion tool.
- Owner-local CLI commands: grant, grants, revoke, pending, approve, reject, audit.
- Pending edits record original SHA-256 and proposed SHA-256. Applying an edit checks that the file still matches the original and the grant remains active.
- Audit stores action, result, grant ID, relative path and timestamp, never raw content.

## Windows PowerShell — owner setup

From the canonical repository:

    cd D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\mf_platform
    python -m app.local_connect.cli --help

Connect one existing project directory for read-only access:

    python -m app.local_connect.cli grant --path "D:\ExampleBusiness\Website" --name "My Website"

To allow proposals for a file edit, deliberately choose readwrite; the local terminal requires the literal phrase GRANT WRITE:

    python -m app.local_connect.cli grant --path "D:\ExampleBusiness\Website" --name "My Website" --access readwrite

See grants and disconnect:

    python -m app.local_connect.cli grants
    python -m app.local_connect.cli revoke --id YOUR_GRANT_ID

Review proposed changes and explicitly approve or reject:

    python -m app.local_connect.cli pending
    python -m app.local_connect.cli approve --id YOUR_PROPOSAL_ID
    python -m app.local_connect.cli reject --id YOUR_PROPOSAL_ID

The approve command shows a unified diff in the owner's local terminal and requires the exact text APPROVE YOUR_PROPOSAL_ID. It refuses oversized diffs or stale file contents rather than silently applying a change.

Inspect recent activity:

    python -m app.local_connect.cli audit

## Private MCP server

The installed Python interpreter must have the official mcp package. Start the stdio MCP server for an approved local client with:

    python -m app.local_connect.mcp_server

Or:

    python -m app.local_connect.cli server

These commands speak MCP on stdin/stdout, not HTTP. An authorized local IDE or MCP client must spawn the process. Do not run them as a public web server. No live MeasuredField ChatGPT plugin connection has been configured by this task.

## Future ChatGPT integration

OpenAI Secure MCP Tunnel may be used for a private developer test without inbound ports. It requires explicit owner opt-in, a Platform tunnel ID and runtime credentials, supported workspace/organization permissions and the tunnel-client running on the authorized computer. This task does not configure that tunnel or transmit project files.

A distributed commercial connection additionally requires OAuth 2.1, verification of issuer/audience/expiry/scopes, owner/device/tenant pairing, permission-revocation receipts, secure remote MCP transport, independent audit, installer and onboarding. A tunnel by itself is not a customer access-control or identity system.

References:
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://developers.openai.com/plugins/build/auth
- docs/MEASUREDFIELD_MCP.md

## Explicit limits and threat boundaries

1. No default computer access and no drive-root grants. A customer chooses each folder.
2. Credential-like files and directories are excluded, including .env, .ssh, .git and private keys.
3. Absolute/traversal paths, Windows device filenames, symlinks, junctions and other reparse points are refused.
4. No shell, installer, deletion, live publishing or batch edits are available to the model.
5. Only UTF-8 text up to 256 KiB can be read/changed; folder browsing is limited to 100 entries.
6. Proposed text is stored in the local SQLite database, protected by the OS user's profile. Production needs stronger OS-native ACL/encryption, lifecycle and retention controls.
7. There are rechecks immediately before writes, but this pilot is not a kernel-level guarantee against a malicious local process racing filesystem changes. Production requires open-handle no-follow security.
8. The approval CLI is local and separate from AI-callable MCP tools. No customer files, production sites or credentials are modified by development tests.
9. The current UX uses a terminal, not yet the nontechnical one-click connection experience. The product should hide the technical steps behind a trustworthy graphical interface.

## Verification

    python -B -m pytest -q tests/test_local_connect.py
    python -B scripts/run_checks.py

These tests only validate the local prototype, not a real ChatGPT OAuth connection or cross-tenant production rollout.

## Next engineering slices

1. Friendly connection/setup window: Choose folder, name project, choose read or propose-edits, view/revoke on one screen.
2. Authenticated paired device and private Secure MCP Tunnel test under a real approved account.
3. Server-side OAuth and cross-tenant authorization plus signed owner action approvals.
4. A Git-diff/test/preview/publish workflow with customer approval and rollback.
5. Signed Windows packaging, auto-update, installer, logs, and recovery for a business owner who knows no code.

Product success criterion: a business owner can say, "Work on the website in this folder, show me what changed, and don't publish until I approve."
