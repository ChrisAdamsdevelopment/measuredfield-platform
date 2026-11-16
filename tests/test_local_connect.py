"""Synthetic adversarial coverage for owner-approved local project files."""
from pathlib import Path

import pytest

from app.local_connect import LocalConnect, AccessDenied, ConflictError
from app.local_connect.cli import parser


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "readme.md").write_text("Before\n", encoding="utf-8")
    (root / "pages").mkdir()
    (root / "pages" / "home.txt").write_text("homepage", encoding="utf-8")
    return LocalConnect(tmp_path / "state"), root


def approved(env, access="read"):
    local, root = env
    return local.grant("Business", root, access)["id"]


def test_initial_state_denies_everything(env):
    local, _ = env
    assert local.grants() == []
    assert local.pending() == []
    with pytest.raises(AccessDenied):
        local.read("not-a-grant", "readme.md")


def test_grant_is_bounded_and_named(env):
    local, root = env
    gid = approved(env)
    assert local.grants()[0]["id"] == gid
    assert local.grants()[0]["access"] == "read"
    assert "root" not in local.grants()[0]
    assert local.grants(include_roots=True)[0]["root"] == str(root.resolve())


def test_browse_and_read_real_files(env):
    local, _ = env
    gid = approved(env)
    items = local.browse(gid)["items"]
    assert {x["name"] for x in items} == {"pages", "readme.md"}
    assert local.read(gid, "pages/home.txt")["content"] == "homepage"
    assert len(local.read(gid, "readme.md")["sha256"]) == 64


@pytest.mark.parametrize("relative", [
    "../outside", "nested/../outside", "/absolute", r"C:\Windows\win.ini",
    r"\\server\share\file", ".git/config", ".ssh/id_rsa", ".env",
    "config.pem", "credentials.json", "CON", "NUL.txt", "a//b",
    "./readme.md", "abc:stream", "file. ", "name\x00evil",
])
def test_unsafe_paths_denied(env, relative):
    local, _ = env
    gid = approved(env)
    with pytest.raises(AccessDenied):
        local.read(gid, relative)


def test_sensitive_entries_not_exposed(env):
    local, root = env
    (root / ".env").write_text("FAKE_TOKEN=sample", encoding="utf-8")
    (root / ".git").mkdir()
    gid = approved(env)
    assert ".env" not in str(local.browse(gid))
    assert ".git" not in str(local.browse(gid))


def test_root_drive_access_denied(env):
    local, root = env
    with pytest.raises(AccessDenied):
        local.grant("Everything", Path(root.anchor))


def test_symlink_escape_rejected(env, tmp_path):
    local, root = env
    target = tmp_path / "private.txt"
    target.write_text("no", encoding="utf-8")
    try:
        (root / "alias.txt").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("OS does not allow symlink creation")
    gid = approved(env)
    with pytest.raises(AccessDenied):
        local.read(gid, "alias.txt")


def test_text_only_and_size_limits(env):
    local, root = env
    (root / "binary.bin").write_bytes(b"a\x00b")
    (root / "huge.txt").write_bytes(b"a" * (256 * 1024 + 1))
    (root / "invalid.txt").write_bytes(b"\xff")
    gid = approved(env)
    for name in ("binary.bin", "huge.txt", "invalid.txt"):
        with pytest.raises(AccessDenied):
            local.read(gid, name)


def test_readonly_cannot_propose(env):
    local, _ = env
    with pytest.raises(AccessDenied):
        local.propose(approved(env), "readme.md", "After")


def test_proposal_only_pending_no_write(env):
    local, root = env
    gid = approved(env, "readwrite")
    proposal = local.propose(gid, "readme.md", "After\n")
    assert proposal["owner_approval_required"] is True
    assert (root / "readme.md").read_text(encoding="utf-8") == "Before\n"
    assert len(local.pending()) == 1
    assert "After" not in str(local.pending())


def test_owner_apply_and_duplicate_refused(env):
    local, root = env
    gid = approved(env, "readwrite")
    pid = local.propose(gid, "readme.md", "After\n")["proposal_id"]
    assert "After" in local.diff(pid)
    assert local.approve(pid)["applied"] is True
    assert (root / "readme.md").read_text(encoding="utf-8") == "After\n"
    with pytest.raises(AccessDenied):
        local.approve(pid)


def test_conflict_when_file_changed(env):
    local, root = env
    gid = approved(env, "readwrite")
    pid = local.propose(gid, "readme.md", "After")["proposal_id"]
    (root / "readme.md").write_text("Owner changed", encoding="utf-8")
    with pytest.raises(ConflictError):
        local.approve(pid)
    assert (root / "readme.md").read_text(encoding="utf-8") == "Owner changed"


def test_owner_can_approve_new_file(env):
    local, root = env
    gid = approved(env, "readwrite")
    pid = local.propose(gid, "pages/new.txt", "New")["proposal_id"]
    assert not (root / "pages" / "new.txt").exists()
    local.approve(pid)
    assert (root / "pages" / "new.txt").read_text(encoding="utf-8") == "New"


def test_new_file_created_by_someone_else_conflicts(env):
    local, root = env
    gid = approved(env, "readwrite")
    pid = local.propose(gid, "pages/new.txt", "AI draft")["proposal_id"]
    (root / "pages" / "new.txt").write_text("Owner draft", encoding="utf-8")
    with pytest.raises(ConflictError):
        local.approve(pid)
    assert (root / "pages" / "new.txt").read_text(encoding="utf-8") == "Owner draft"


def test_revoke_also_revokes_pending(env):
    local, _ = env
    gid = approved(env, "readwrite")
    pid = local.propose(gid, "readme.md", "After")["proposal_id"]
    assert local.revoke(gid) is True
    assert local.pending() == []
    with pytest.raises(AccessDenied):
        local.approve(pid)
    with pytest.raises(AccessDenied):
        local.read(gid, "readme.md")


def test_rejection_is_non_mutating(env):
    local, root = env
    gid = approved(env, "readwrite")
    pid = local.propose(gid, "readme.md", "After")["proposal_id"]
    assert local.reject(pid) is True
    assert local.reject(pid) is False
    assert (root / "readme.md").read_text(encoding="utf-8") == "Before\n"


def test_persistence_and_audit_privacy(env):
    local, root = env
    gid = approved(env, "readwrite")
    local.read(gid, "readme.md")
    local.propose(gid, "readme.md", "This is draft content never logged")
    reopened = LocalConnect(local.home)
    assert reopened.grants()[0]["id"] == gid
    events = reopened.audit()
    assert {e["action"] for e in events} >= {"grant", "read", "propose_edit"}
    assert "draft content" not in str(events)
    assert str(root) not in str(events)



def test_cli_diff_escapes_terminal_control_and_bidi(env, monkeypatch, capsys):
    from app.local_connect.cli import main, _safe_terminal_diff
    local, root = env
    gid = approved(env, "readwrite")
    malicious = "Before\n\x1b[2K\rFAKE OK\n\u202eHIDDEN\n"
    pid = local.propose(gid, "readme.md", malicious)["proposal_id"]
    monkeypatch.setenv("MEASUREDFIELD_LOCAL_CONNECT_HOME", str(local.home))
    monkeypatch.setattr("builtins.input", lambda _: "DECLINE")
    assert main(["approve", "--id", pid]) == 2
    printed = capsys.readouterr().out
    assert "\x1b" not in printed
    assert "\r" not in printed
    assert "\u202e" not in printed
    assert r"\u001b" in printed
    assert r"\u202e" in printed
    assert (root / "readme.md").read_text(encoding="utf-8") == "Before\n"
    assert r"\u001b" in _safe_terminal_diff("\x1b")


def test_cli_closed_stdin_is_denied_without_traceback(env, monkeypatch, capsys):
    from app.local_connect.cli import main
    local, root = env
    monkeypatch.setenv("MEASUREDFIELD_LOCAL_CONNECT_HOME", str(local.home))
    def no_input(_):
        raise EOFError("closed stdin")
    monkeypatch.setattr("builtins.input", no_input)
    assert main(["grant", "--path", str(root), "--name", "Example", "--access", "readwrite"]) == 1
    assert local.grants() == []
    assert "Action denied" in capsys.readouterr().err

def test_owner_cli_has_no_default_write_grant():
    args = parser().parse_args(["grant", "--name", "Business", "--path", "C:/demo"])
    assert args.access == "read"


def test_mcp_has_no_model_call_approve_grant_or_shell():
    from app.local_connect import mcp_server
    for name in ("list_granted_projects", "read_project_file",
                 "browse_project", "propose_project_edit"):
        assert callable(getattr(mcp_server, name))
    for forbidden in ("approve_project_edit", "grant_project", "execute_shell"):
        assert not hasattr(mcp_server, forbidden)


def test_real_stdio_mcp_handshake_and_read(env):
    import asyncio
    import os
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    local, root = env
    gid = approved(env)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.local_connect.mcp_server"],
        env={**os.environ, "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(local.home)},
    )

    async def exercise():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                result = await client.list_tools()
                names = {tool.name for tool in result.tools}
                assert "read_project_file" in names
                assert "propose_project_edit" in names
                assert "approve_project_edit" not in names
                assert "execute_shell" not in names
                returned = await client.call_tool(
                    "read_project_file",
                    {"grant_id": gid, "relative_path": "readme.md"},
                )
                assert returned.isError is not True
                assert "Before" in str(returned.content)
    asyncio.run(exercise())


def test_sqlite_connections_close_after_every_operation(tmp_path):
    """Windows must be able to rename/delete the state DB without forced GC."""
    import tempfile
    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        base = Path(directory)
        project = base / "example_business"
        project.mkdir()
        (project / "page.txt").write_text("Original", encoding="utf-8")
        local = LocalConnect(base / "state")
        grant_id = local.grant("Example", project, "readwrite")["id"]
        local.browse(grant_id)
        local.read(grant_id, "page.txt")
        proposal_id = local.propose(grant_id, "page.txt", "Approved")["proposal_id"]
        local.pending()
        local.diff(proposal_id)
        local.approve(proposal_id)
        local.audit()
        local.revoke(grant_id)
        # On Windows an unclosed sqlite3 handle makes this rename fail with WinError 32.
        database = local.db_path
        alternate = database.with_name("moved-for-check.sqlite3")
        database.rename(alternate)
        alternate.rename(database)
    assert not base.exists()
