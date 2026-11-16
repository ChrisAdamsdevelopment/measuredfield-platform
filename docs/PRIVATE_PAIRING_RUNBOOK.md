# MeasuredField MF-030: Private Paired Connected Workspace

Status: development-only local pilot. Real scoped MCP functionality is implemented and tested, but no production OAuth or ChatGPT account link is established.

## What is now possible

An owner can explicitly create a short-lived private connection to specific already approved project folders. Each connection has an ID, a random locally held secret, read-only access by default, expiry, and instant revocation. Both the connection and original folder permission must allow edit proposals before the model can stage a change; a separate human approval is still mandatory to apply it.

The paired MCP server exposes only list/browse/read/propose/pending/activity. Pairing, granting folders, approving changes, deleting files, running shell commands and publishing sites are not model-callable.

The session is checked on every operation, even if a process is already running. Scoped list responses exclude unrelated folders. Session secrets are kept in the local OS user's application directory and never printed in a tool result, CLI output, command argument or audit event.

## Local development commands (use synthetic folders only)

From the canonical mf_platform repository:

    python -m app.local_connect.cli grants
    python -m app.paired_connect.cli --help

After the owner chooses an existing synthetic project folder, pair one specific grant for 15 minutes:

    python -m app.paired_connect.cli pair --name "Private test" --grant-id YOUR_GRANT_ID --ttl-minutes 15

The local terminal requires typing PAIR. Edit proposal permission is an optional extra, requiring the folder's readwrite grant and a second ALLOW SUGGESTIONS confirmation.

Inspect and revoke:

    python -m app.paired_connect.cli connections
    python -m app.paired_connect.cli revoke --id YOUR_PAIRING_ID
    python -m app.paired_connect.cli audit

The local MCP client or future approved private tunnel can spawn this command as a stdio server:

    python -m app.paired_connect.launch --pair-id YOUR_PAIRING_ID

Never configure a tunnel to launch the older unpaired app.local_connect.mcp_server; use the paired launcher only.

This is a process intended for MCP clients, not a URL or user-facing website. The command contains only a non-secret pairing ID. The secret is loaded internally from the local user's private state. A signed, friendly Windows installer and GUI pairing controls remain to be built.

## Current official OpenAI Secure MCP Tunnel path

References:
https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
https://developers.openai.com/api/docs/guides/tools-connectors-mcp
https://developers.openai.com/plugins/build/auth

OpenAI's Secure MCP Tunnel enables outbound-only, private MCP transport without opening an inbound public port. This is a private test capability, NOT a public plugin distribution channel.

Before a live private ChatGPT test, the owner and operator must confirm:
1. The correct OpenAI Platform organization and intended ChatGPT workspace.
2. Tunnels Read + Manage permission to create, and Read + Use to run/select a tunnel. ChatGPT workspace permissions are separate.
3. A tunnel ID and a restricted runtime key, entered locally without displaying secrets in commands, logs, chats, screenshots or code.
4. An official tunnel-client installation from https://github.com/openai/tunnel-client/releases/latest using current help quickstart. Use a stdio profile with the paired launcher (not unpaired server).
5. A successful tunnel-client doctor --profile NAME --explain, and local readiness/active polling evidence.
6. A private ChatGPT custom MCP plugin linked to the SAME tunnel and correct workspace, with an actual permitted read-only synthetic tool call as proof.

Do not claim successful ChatGPT pairing until this last tool call succeeds. No live tunnel, subscription charge, account credential, customer data or plugin registration was created by MF-030.

## Commercial release is a separate engineering gate

A tunnel authenticates the transport to OpenAI; it does not, by itself, authenticate a MeasuredField customer or authorize every remote user. This local pilot is bound to a trusted OS-user session and is not safe to share among unrelated users.

Commercial release requires an independently verified OAuth 2.1 authorization server and protected-resource metadata; signature, issuer, audience, expiry and scope verification; PKCE S256; real tenant/owner/device identity; per-device pairing and revocation; secure key storage (such as Windows DPAPI); signed installer and update chain; audit/retention controls; and a separately approved public HTTPS MCP resource server. Public distribution cannot rely on private Secure MCP Tunnel alone.

Limits: current pairing expires in 1 to 1,440 minutes, maximum 8 selected grants, and uses Windows user-profile-local secret storage. It checks revocation before each operation but does not atomically cancel an operation already running. The original local companion reads only bounded UTF-8 text, blocks sensitive credential paths, and requires owner approval to apply file edits. No shell, auto-deploy or customer access was introduced.

## Automated verification

    python -B -m pytest -q tests/test_paired_connect.py
    python -B scripts/run_checks.py
    python -B scripts/run_disposable_pg_audit.py --full-suite

Focused tests include a real Python MCP stdio client, scoped synthetic file reads, cross-project denial, an unauthorized proposal rejection, expiry, tampered credential denial, owner-only confirmation, revocation of an active session, and SQLite handle release on Windows.

## Next owner-facing milestone

Add a Connected Workspace "Pair trusted connection" tab showing the exact folders, permissions, expiration and a Disconnect button. Then complete the independently reviewed OAuth and private tunnel configuration for one permitted real-world test. Keep the first screen in business-owner language: Connect this computer, Choose folders, Read only, Suggest edits, Review changes, Disconnect now.
