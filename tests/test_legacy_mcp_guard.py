"""MF-046 — legacy unpaired MCP fail-closed startup and per-call checks.

Synthetic project and account metadata only. No real device, credential,
workspace API, live network, customer content or remote tunnel.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.local_connect import AccessDenied, LocalConnect
from app.local_connect import mcp_server
from app.local_connect.cli import main as cli_main


def _mcp_is_error(result) -> bool:
    """Fail closed across MCP 1.x isError and MCP 2.x is_error result fields."""
    old = getattr(result, "isError", None)
    new = getattr(result, "is_error", None)
    if old is not None and new is not None and old != new:
        raise AssertionError("Conflicting MCP tool error flags")
    flag = new if new is not None else old
    if not isinstance(flag, bool):
        raise AssertionError("MCP tool result did not specify an error status")
    return flag


@pytest.fixture
def site(tmp_path, monkeypatch):
    home = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    (project / "hello.txt").write_text("synthetic-owner-file", encoding="utf-8")
    monkeypatch.setenv("MEASUREDFIELD_LOCAL_CONNECT_HOME", str(home))
    local = LocalConnect(home)
    grant = local.grant("Demo", project, "readwrite")["id"]
    return {
        "home": home, "project": project, "grant": grant,
        "state": home / "account_connection.json",
        "credential": home / "account_device.dpapi",
        "local": local,
    }


def add_account_state(site, status="active"):
    site["state"].write_text(
        json.dumps({
            "status": status, "device_id": "synthetic-device",
            "server_url": "https://workspace.synthetic.invalid",
            "access": "suggest",
        }),
        encoding="utf-8",
    )


def test_clean_never_linked_pilot_remains_scoped(site):
    grant = site["grant"]
    assert mcp_server.list_granted_projects()[0]["id"] == grant
    assert mcp_server.browse_project(grant)["items"]
    assert mcp_server.read_project_file(grant, "hello.txt")["content"] == "synthetic-owner-file"
    proposal = mcp_server.propose_project_edit(grant, "hello.txt", "not yet applied")
    assert proposal["owner_approval_required"] is True
    assert (site["project"] / "hello.txt").read_text(encoding="utf-8") == "synthetic-owner-file"
    assert mcp_server.list_pending_edits()
    assert isinstance(mcp_server.recent_connection_activity(), list)


@pytest.mark.parametrize("status", [
    "active", "revoked", "pending", "not_connected", "offline",
])
def test_account_state_refuses_all_unpaired_file_calls(site, status):
    add_account_state(site, status)
    grant = site["grant"]
    calls = (
        lambda: mcp_server.list_granted_projects(),
        lambda: mcp_server.browse_project(grant),
        lambda: mcp_server.read_project_file(grant, "hello.txt"),
        lambda: mcp_server.propose_project_edit(grant, "hello.txt", "bad"),
        lambda: mcp_server.list_pending_edits(),
        lambda: mcp_server.recent_connection_activity(),
    )
    for fn in calls:
        with pytest.raises(AccessDenied):
            fn()
    assert site["local"].pending() == []


def test_leftover_credential_without_state_is_not_unlinked(site):
    site["credential"].write_bytes(b"synthetic-unreadable-blob")
    with pytest.raises(AccessDenied, match="legacy"):
        mcp_server.read_project_file(site["grant"], "hello.txt")


@pytest.mark.parametrize("payload", ["{not json", "null", "[]"])
def test_corrupt_connection_state_fails_closed(site, payload):
    site["state"].write_text(payload, encoding="utf-8")
    with pytest.raises(AccessDenied):
        mcp_server.read_project_file(site["grant"], "hello.txt")


def test_account_link_created_after_first_read_blocks_persistent_tools(site):
    grant = site["grant"]
    assert mcp_server.read_project_file(grant, "hello.txt")["content"] == "synthetic-owner-file"
    add_account_state(site, "revoked")
    with pytest.raises(AccessDenied):
        mcp_server.read_project_file(grant, "hello.txt")
    with pytest.raises(AccessDenied):
        mcp_server.propose_project_edit(grant, "hello.txt", "not allowed")
    assert site["local"].pending() == []


def test_link_created_during_operation_rejects_result(site, monkeypatch):
    original = LocalConnect.read

    def newly_linked_during_read(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        add_account_state(site)
        return result

    monkeypatch.setattr(LocalConnect, "read", newly_linked_during_read)
    with pytest.raises(AccessDenied):
        mcp_server.read_project_file(site["grant"], "hello.txt")


def test_linked_server_refuses_start_before_mcp_stdio(site, monkeypatch, capsys):
    add_account_state(site)
    run = Mock()
    monkeypatch.setattr(mcp_server.mcp, "run", run)
    with pytest.raises(AccessDenied, match="paired connection launcher"):
        mcp_server.main()
    assert run.call_count == 0
    assert cli_main(["server"]) == 1
    assert "Action denied" in capsys.readouterr().err
    assert run.call_count == 0


def test_clean_server_allows_private_stdio_start(site, monkeypatch):
    run = Mock()
    monkeypatch.setattr(mcp_server.mcp, "run", run)
    mcp_server.main()
    run.assert_called_once_with(transport="stdio")


def test_separate_long_lived_python_process_refuses_later_link(site):
    """Reproduce a running tool process whose owner links after the first read."""
    script = """
import json, os, sys
from app.local_connect import mcp_server
grant = os.environ['MF_SYNTHETIC_GRANT']
for command in sys.stdin:
    if command.strip() == 'stop':
        break
    try:
        row = mcp_server.read_project_file(grant, 'hello.txt')
        print('OK:' + row['content'], flush=True)
    except Exception as exc:
        print('DENIED:' + exc.__class__.__name__, flush=True)
"""
    env = {
        **os.environ,
        "MF_SYNTHETIC_GRANT": site["grant"],
        "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(site["home"]),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        process.stdin.write("read\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "OK:synthetic-owner-file"
        add_account_state(site, "revoked")
        process.stdin.write("read\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "DENIED:AccessDenied"
        process.stdin.write("stop\n")
        process.stdin.flush()
        process.wait(timeout=10)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_actual_separate_stdio_mcp_process_denies_after_link(site):
    """An already initialized official MCP session must lose file access."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {
        **os.environ,
        "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(site["home"]),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "MF_ENV_FILE": "",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "app.local_connect.mcp_server"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    async def scenario():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                arguments = {
                    "grant_id": site["grant"],
                    "relative_path": "hello.txt",
                }
                before = await session.call_tool("read_project_file", arguments)
                assert _mcp_is_error(before) is False
                assert "synthetic-owner-file" in str(before.content)
                add_account_state(site, "revoked")
                after = await session.call_tool("read_project_file", arguments)
                assert _mcp_is_error(after) is True
                assert "synthetic-owner-file" not in str(after.content)
                proposal = await session.call_tool("propose_project_edit", {
                    **arguments, "new_content": "forbidden",
                })
                assert _mcp_is_error(proposal) is True
                assert site["local"].pending() == []

    asyncio.run(scenario())


def test_linked_direct_stdio_start_exits_before_protocol(site):
    add_account_state(site, "active")
    env = {
        **os.environ,
        "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(site["home"]),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "MF_ENV_FILE": "",
    }
    result = subprocess.run(
        [sys.executable, "-B", "-m", "app.local_connect.mcp_server"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env, input="", capture_output=True, text=True, timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert "synthetic-owner-file" not in (result.stdout + result.stderr)



def test_mcp_error_flag_is_strict_for_both_sdk_spellings():
    from types import SimpleNamespace

    assert _mcp_is_error(SimpleNamespace(isError=True)) is True
    assert _mcp_is_error(SimpleNamespace(is_error=True)) is True
    assert _mcp_is_error(SimpleNamespace(isError=False)) is False
    assert _mcp_is_error(SimpleNamespace(is_error=False)) is False
    with pytest.raises(AssertionError, match="did not specify"):
        _mcp_is_error(SimpleNamespace())
    with pytest.raises(AssertionError, match="did not specify"):
        _mcp_is_error(SimpleNamespace(is_error="false"))
    with pytest.raises(AssertionError, match="Conflicting"):
        _mcp_is_error(SimpleNamespace(is_error=True, isError=False))
