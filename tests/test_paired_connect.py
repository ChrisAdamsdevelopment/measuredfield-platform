"""Adversarial private, scoped local pairing tests. All files are synthetic."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from app.local_connect import AccessDenied
from app.paired_connect.registry import BoundSession, PairingRegistry


def _tool_failed(result):
    """Support error status on pinned MCP 2.x and legacy 1.x."""
    for field in ("is_error", "isError"):
        if hasattr(result, field):
            return bool(getattr(result, field))
    raise AssertionError("MCP response did not expose an error status")


@pytest.fixture
def setup(tmp_path: Path):
    home = tmp_path / "state"
    registry = PairingRegistry(home)
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    third = tmp_path / "ungranted"
    for project in (first, second, third):
        project.mkdir()
        (project / "note.txt").write_text("Contents from " + project.name, encoding="utf-8")
    alpha = registry.connect.grant("Alpha company", first, "readwrite")["id"]
    beta = registry.connect.grant("Beta company", second, "read")["id"]
    return registry, home, alpha, beta, first, second, third


def create(registry, grants, *, can_propose=False, mins=15, name="Local pilot"):
    return registry.create(
        name, grants, allow_proposals=can_propose,
        expires_minutes=mins, owner_confirmed=True,
    )["pairing_id"]


def session(registry, pair_id):
    secret = registry.token_for_local_launcher(pair_id)
    return BoundSession(pair_id, secret, registry.home)


def test_starts_with_zero_pairings(setup):
    registry, *_ = setup
    assert registry.list() == []


def test_pairing_requires_explicit_owner_confirmation(setup):
    registry, _, alpha, *_ = setup
    with pytest.raises(AccessDenied):
        registry.create("Unapproved", [alpha])
    assert registry.list() == []


@pytest.mark.parametrize("ttl", [0, -1, 1441, 1.2])
def test_invalid_ttl_denied(setup, ttl):
    registry, _, alpha, *_ = setup
    with pytest.raises(AccessDenied):
        registry.create("Session", [alpha], owner_confirmed=True, expires_minutes=ttl)


def test_cannot_pair_unknown_grant(setup):
    registry, _, alpha, *_ = setup
    with pytest.raises(AccessDenied):
        registry.create("Session", ["not-real"], owner_confirmed=True)
    assert registry.list() == []


def test_duplicate_or_no_grants_denied(setup):
    registry, _, alpha, *_ = setup
    for ids in ([], [alpha, alpha]):
        with pytest.raises(AccessDenied):
            registry.create("Session", ids, owner_confirmed=True)


def test_pair_starts_read_only_and_hides_secret(setup):
    registry, _, alpha, *_ = setup
    pair_id = create(registry, [alpha])
    public = registry.list()[0]
    assert public["pairing_id"] == pair_id
    assert public["status"] == "active"
    assert public["can_suggest_edits"] is False
    secret = registry.token_for_local_launcher(pair_id)
    assert len(secret) > 32
    assert secret not in str(public)
    assert secret not in str(registry.audit())
    assert secret not in str(registry.connect.audit())


def test_grants_are_scoped_to_selected_projects(setup):
    registry, _, alpha, beta, *_ = setup
    s = session(registry, create(registry, [alpha]))
    assert [p["id"] for p in s.projects()] == [alpha]
    assert "alpha" in s.read(alpha, "note.txt")["content"]
    with pytest.raises(AccessDenied):
        s.read(beta, "note.txt")


def test_explicit_proposal_permission_required_even_if_folder_is_writable(setup):
    registry, _, alpha, _, first, *_ = setup
    s = session(registry, create(registry, [alpha]))
    with pytest.raises(AccessDenied):
        s.propose(alpha, "note.txt", "Changed")
    assert "Contents" in (first / "note.txt").read_text(encoding="utf-8")


def test_pair_can_propose_only_in_writable_selected_folder(setup):
    registry, _, alpha, beta, first, *_ = setup
    s = session(registry, create(registry, [alpha, beta], can_propose=True))
    proposal = s.propose(alpha, "note.txt", "Suggested copy")
    assert proposal["owner_approval_required"] is True
    assert "Contents" in (first / "note.txt").read_text(encoding="utf-8")
    with pytest.raises(AccessDenied):
        s.propose(beta, "note.txt", "Illegal write")
    assert len(s.pending()) == 1


def test_pair_cannot_enable_proposals_for_read_only_grant(setup):
    registry, _, alpha, beta, *_ = setup
    with pytest.raises(AccessDenied):
        create(registry, [beta], can_propose=True)


def test_revoke_is_immediate_during_active_session(setup):
    registry, _, alpha, *_ = setup
    pair_id = create(registry, [alpha])
    s = session(registry, pair_id)
    assert "alpha" in s.read(alpha, "note.txt")["content"]
    assert registry.revoke(pair_id)
    with pytest.raises(AccessDenied):
        s.read(alpha, "note.txt")
    with pytest.raises(AccessDenied):
        registry.token_for_local_launcher(pair_id)
    assert registry.list()[0]["status"] == "revoked"


def test_revoking_folder_also_revokes_access_for_that_folder(setup):
    registry, _, alpha, beta, *_ = setup
    pair_id = create(registry, [alpha, beta])
    s = session(registry, pair_id)
    registry.connect.revoke(alpha)
    with pytest.raises(AccessDenied):
        s.browse(alpha)
    assert "beta" in s.read(beta, "note.txt")["content"]
    assert [p["id"] for p in s.projects()] == [beta]


def test_pair_expiration_prevents_reuse(setup):
    registry, _, alpha, *_ = setup
    pair_id = create(registry, [alpha], mins=1)
    token = registry.token_for_local_launcher(pair_id)
    with sqlite3.connect(registry.db_path) as db:
        db.execute("UPDATE pairings SET expires_at=? WHERE id=?", (time.time() - 10, pair_id))
    with pytest.raises(AccessDenied):
        registry.authorize(pair_id, token, alpha, "read")
    with pytest.raises(AccessDenied):
        registry.token_for_local_launcher(pair_id)


def test_tampered_token_denied(setup):
    registry, _, alpha, *_ = setup
    pair_id = create(registry, [alpha])
    token = registry.token_for_local_launcher(pair_id)
    with pytest.raises(AccessDenied):
        registry.authorize(pair_id, token + "X", alpha, "read")


def test_no_cross_pairing_credential_reuse(setup):
    registry, _, alpha, beta, *_ = setup
    pair_a = create(registry, [alpha])
    pair_b = create(registry, [beta])
    with pytest.raises(AccessDenied):
        registry.authorize(pair_b, registry.token_for_local_launcher(pair_a), beta, "read")


def test_activity_only_for_grants_in_pairing(setup):
    registry, _, alpha, beta, *_ = setup
    registry.connect.read(alpha, "note.txt")
    registry.connect.read(beta, "note.txt")
    s = session(registry, create(registry, [alpha]))
    events = s.activity()
    assert events
    assert all(e["grant_id"] == alpha for e in events)


def test_public_pairing_metadata_has_no_tokens_or_local_roots(setup):
    registry, _, alpha, beta, first, second, *_ = setup
    pair_id = create(registry, [alpha, beta])
    metadata = registry.list()
    audit = registry.audit()
    assert str(first) not in str(metadata) + str(audit)
    assert str(second) not in str(metadata) + str(audit)
    assert registry.token_for_local_launcher(pair_id) not in str(metadata) + str(audit)


def test_token_state_directory_is_deletable_without_database_leaks(tmp_path):
    home = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("Sample", encoding="utf-8")
    r = PairingRegistry(home)
    grant = r.connect.grant("Local", project)["id"]
    pid = create(r, [grant])
    assert session(r, pid).read(grant, "note.txt")["content"] == "Sample"
    assert r.revoke(pid)
    # Windows will fail these renames if SQLite connections weren't closed.
    original = r.db_path
    moved = original.with_name("renamed.sqlite3")
    original.rename(moved)
    moved.rename(original)


def test_owner_only_command_has_confirmation_requirements(setup, monkeypatch):
    from app.paired_connect.cli import main
    registry, home, alpha, *_ = setup
    monkeypatch.setenv("MEASUREDFIELD_LOCAL_CONNECT_HOME", str(home))
    monkeypatch.setattr("builtins.input", lambda _: "NO")
    assert main(["pair", "--name", "Nope", "--grant-id", alpha]) == 2
    assert registry.list() == []
    monkeypatch.setattr("builtins.input", lambda _: "PAIR")
    assert main(["pair", "--name", "Okay", "--grant-id", alpha]) == 0
    assert len(registry.list()) == 1


def test_mcp_interface_has_no_grant_or_pairing_tools():
    from app.paired_connect import server
    assert hasattr(server, "mcp")
    for name in ["list_granted_projects", "browse_project", "read_project_file",
                 "propose_project_edit", "list_pending_edits", "recent_connection_activity"]:
        assert callable(getattr(server, name))
    for forbidden in ("approve_edit", "approve_project_edit", "grant_project",
                      "create_pairing", "revoke_connection", "run_shell", "delete_file"):
        assert not hasattr(server, forbidden)


def test_actual_mcp_client_stdio_pairing_scope_and_live_revoke(setup, monkeypatch):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    registry, home, alpha, beta, *_ = setup
    pair_id = create(registry, [alpha])
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.paired_connect.launch", "--pair-id", pair_id],
        env={**os.environ, "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(home)},
    )

    async def exercise():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                listing = await client.list_tools()
                names = {tool.name for tool in listing.tools}
                assert "read_project_file" in names
                assert "approve_project_edit" not in names
                allowed = await client.call_tool("list_granted_projects", {})
                assert _tool_failed(allowed) is False
                assert alpha in str(allowed.content)
                assert beta not in str(allowed.content)
                read = await client.call_tool("read_project_file", {
                    "grant_id": alpha, "relative_path": "note.txt"
                })
                assert _tool_failed(read) is False
                assert "alpha" in str(read.content)
                outside = await client.call_tool("read_project_file", {
                    "grant_id": beta, "relative_path": "note.txt"
                })
                assert _tool_failed(outside) is True
                denied = await client.call_tool("propose_project_edit", {
                    "grant_id": alpha, "relative_path": "note.txt",
                    "new_content": "Must not edit"
                })
                assert _tool_failed(denied) is True
                assert registry.revoke(pair_id)
                after = await client.call_tool("read_project_file", {
                    "grant_id": alpha, "relative_path": "note.txt"
                })
                assert _tool_failed(after) is True
    asyncio.run(exercise())


def test_boolean_expiry_not_accepted_as_minutes(setup):
    registry, _, alpha, *_ = setup
    with pytest.raises(AccessDenied):
        registry.create("Invalid bool TTL", [alpha], owner_confirmed=True, expires_minutes=True)


def test_denied_authorization_records_metadata_not_token(setup):
    registry, _, alpha, *_ = setup
    pid = create(registry, [alpha])
    fake_token = "x" * 64
    with pytest.raises(AccessDenied):
        registry.authorize(pid, fake_token, alpha, "read")
    records = registry.audit()
    assert any(e["operation"] == "authorize" and e["outcome"] == "denied" for e in records)
    assert fake_token not in str(records)


def test_activity_overfetches_other_project_noise_without_cross_scope_leak(setup):
    registry, _, alpha, beta, *_ = setup
    registry.connect.read(alpha, "note.txt")
    for _ in range(25):
        registry.connect.read(beta, "note.txt")
    s = session(registry, create(registry, [alpha]))
    events = s.activity(limit=1)
    assert len(events) == 1
    assert events[0]["grant_id"] == alpha
