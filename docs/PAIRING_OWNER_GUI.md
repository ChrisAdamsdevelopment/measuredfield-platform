# MF-031: Owner-friendly trusted connections

This is the local Windows graphical interface for owner-approved, temporary MCP pairings. It builds on MF-027–030 without opening a public endpoint, connecting an actual ChatGPT account or transmitting any customer files.

## Start

From the canonical MeasuredField mf_platform repository:

    python -m app.local_connect_ui

Or double-click the existing local launcher at:

    app\local_connect_ui\launch_connected_workspace.cmd

In Connected Workspace:
1. Open **My connected folders** and choose the project folders you want the assistant to see. Read-only is the default. Allow suggestions only if you want AI-proposed edits, which still require separate approval.
2. Open **Trusted connections**. Select one or more project folders. The list shows their actual location on this computer.
3. Name your private test connection and select an expiration (5, 15, 30, 60, 120, 480, or 1440 minutes). The default is 15 minutes. The connection expires automatically.
4. Optionally check **Allow the assistant to suggest edits**, only if at least one selected folder was granted that permission. The UI then requires a second confirmation.
5. Click **Pair selected folders** and read the exact permission and folder summary. Approve it explicitly.
6. View the new entry under **Trusted connections on this computer**. Its status and expiration are visible. Click **Disconnect selected connection** to immediately revoke it. Revocation is effective on the next MCP call, including an already-running local test session.

**Important:** The owner screen creates a short-lived local pairing; it does NOT start a tunnel, authenticate a ChatGPT user, launch a remote MCP client, enable background access or install a plugin. A live ChatGPT connection requires owner-authorized OpenAI Platform setup and verified endpoint/tunnel tool use.

## Security and behavior

- No drive-wide implicit access; only specifically selected existing project grants.
- Pairing defaults to read-only; separate permission is required to stage suggested edits, and a different local approval is required to apply those edits.
- The model cannot call pairing/grant/revoke/approval functions through the MCP tool surface.
- Session tokens are not displayed in the UI, logs, or MCP results. The UI only shows non-secret connection name, number of folders, scope, expiration and status.
- Canceling any confirmation is a no-op.
- Disconnecting a project folder through the main tab also removes that folder from active paired capabilities on their next check.
- If an owner has several projects, the selected pairing sees only the selected subset.
- No public HTTP port, paid API request, production deployment, customer data access, or background agent.

## Developer validation

    python -B -m pytest -q tests/test_pairing_owner_ui.py tests/test_paired_connect.py tests/test_local_connect_ui.py
    python -B scripts/run_disposable_pg_audit.py --full-suite

The synthetic tests cover approval, read-only default, second edit confirmation, expiry, cross-grant denial, revocation, no token exposure and actual native Tkinter panel button flow. The desktop window smoke test uses only an empty synthetic local database.

## Next engineering gates

1. Add a signed and packaged desktop installer, plus clearer first-run onboarding.
2. Configure a private Secure MCP Tunnel under a correctly permissioned OpenAI Platform organization/workspace, then verify an actual read-only ChatGPT tool call with synthetic data. This is not yet complete.
3. Build independently verified OAuth 2.1, per-tenant identity/consent, encrypted OS-native key storage and server-side owner approval receipts for commercial distribution.
4. Connect Local Visibility, AI Visibility, Lead Rescue and website publishing through the same owner-facing workspace, with evidence-backed results and separately reviewed deployment.

## Owner review refinements

The Trusted Connections table now displays exact selected project names, not only a folder count. **View selected connection** shows the specific paths and per-folder permissions, including when an underlying grant has been revoked. **Connection activity** opens a metadata-only history window; raw tokens and file contents remain private. Blank names are rejected before confirmation, and selecting an expired/revoked connection explains that access is already inactive. Windows GUI tests explicitly close the Tk widget graph to avoid lingering interpreter cleanup warnings.
