# Connected Workspace — Windows Owner Window (MF-028)

## Why this exists

Owners should not need PowerShell to tell MeasuredField which project files they want to work on. In the desktop pilot, use familiar language: **Connect a folder**, **Review changes**, **Approve this change**, **Disconnect**. Advanced terminology (MCP, OAuth, scopes) belongs in engineering documentation rather than on the first screen.

This builds on MF-027, the tested owner-granted file companion. It is a **local pilot**, not a registered or connected ChatGPT plugin or a commercial installer. No public deployment has occurred.

## Launch on the development computer

From the canonical mf_platform directory:

    python -m app.local_connect_ui

Or double-click the local launcher:

    app\local_connect_ui\launch_connected_workspace.cmd

The launcher changes to the repository folder and starts Python. It does not open a network connection or silently enable MCP.

## What the owner can do

1. Open **My connected folders** and select **Choose a folder to connect**. A native Windows folder picker appears. Give the project a familiar name, for example "My Construction Website".
2. Access defaults to **Read only**. To allow AI-proposed file changes, select **Let AI suggest file edits** and approve a second explicit confirmation. Even then, the AI cannot apply edits directly.
3. See which projects and paths have permission. **Disconnect selected folder** revokes the grant and cancels its pending edits.
4. In **Review changes**, select a pending change and click **Review selected change**. See the filename, old/new hashes and a readable difference before approving. Control and bidirectional Unicode characters are escaped.
5. **Approve this change** requires separate local confirmation. The owner controller binds the approval to the preview hash. The lower-level companion also checks the original file's current bytes and permission before applying, refusing stale edits.
6. **Reject this change** leaves the project file unchanged. **Activity history** shows action results without full file contents.

The owner window does not create grants, revoke grants or approve edits on behalf of an AI model. These actions are never exposed as MCP tools.

## Supported operations and limitations

- This local window uses SQLite state under the Windows owner's Local AppData directory.
- Only existing, explicitly selected project folders are accessible. Text reads/edits are limited to UTF-8 text up to 256 KiB; no automatic drive scan, shell commands, bulk deletes, publishing or deployment.
- Credential-like files, Windows path traversal, symlinks and junctions are denied by the underlying companion.
- The window does not pair with ChatGPT, install a tunnel or connect to the network. Opening it does not start a background process.
- Python and Tkinter must be available until a signed native installer is built.
- Production hardening is still needed for active local adversaries, OS-native encryption, trusted signing and authenticated user/device identity.
- A connected folder is a local *permission*, not evidence that the MeasuredField public platform or ChatGPT can remotely access files.

## Development verification

    python -B -m pytest -q tests/test_local_connect_ui.py
    python -B -m pytest -q tests/test_local_connect.py
    python -B scripts/run_checks.py

Automated tests exercise the owner controller without opening windows. Real Windows keyboard/mouse accessibility and a customer-consented end-to-end test remain required.

## Next slices

- Private pairing: approved device + organization identity, revocation, outbound authenticated channel and a real, consented ChatGPT session.
- One-click installer: signed native packaging, first-run onboarding, permission handling and recovery.
- Website workflow: preview, Git diff and tests before a separately approved publish. File-edit approval is not deployment approval.
- Evidence-backed modules: bring Local Visibility, AI Visibility and Lead Rescue into the same owner workspace, clearly labeling measured versus estimated information.

## Alignment with market language

Customer-facing terms: Connected Workspace, My connected folders, Review changes, Read only, Suggest edits, Disconnect.

Technical terms kept internal: MCP, stdio, OAuth, SHA-256, grant_id, proposal_id.

Core promise: a business owner can let AI help with their own business files without needing to become a software developer.
