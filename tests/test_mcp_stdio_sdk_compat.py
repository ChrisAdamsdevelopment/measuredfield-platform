"""MF-047 actual stdio protocol compatibility under MCP SDK 1.x and pinned 2.3.

Use global 1.27.1 or isolated MCP 2.3.0 interpreter. Synthetic folders only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.local_connect import LocalConnect
from app.paired_connect.registry import PairingRegistry

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def stdio_demo(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "hello.txt").write_text("synthetic-stdio-sample", encoding="utf-8")
    home = tmp_path / "state"
    monkeypatch.setenv("MEASUREDFIELD_LOCAL_CONNECT_HOME", str(home))
    local = LocalConnect(home)
    grant = local.grant("Synthetic", project, "readwrite")["id"]
    return {"home": home, "local": local, "grant": grant, "project": project}


def denied(result) -> bool:
    """The SDK changed its exposed tool result attribute in major v2."""
    if hasattr(result, "is_error"):
        return bool(result.is_error)
    if hasattr(result, "isError"):
        return bool(result.isError)
    raise AssertionError(f"No status field on MCP {version('mcp')} call result")


def params(module, env):
    return StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", module],
        env={**os.environ, **env, "PYTHONPATH": str(ROOT), "MF_ENV_FILE": ""},
        cwd=str(ROOT),
    )


def call_args(stdio_demo):
    return {"grant_id": stdio_demo["grant"], "relative_path": "hello.txt"}


def run_scenario(mcp_params, work):
    async def scenario():
        async with stdio_client(mcp_params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                await work(session)
    asyncio.run(scenario())


def test_both_private_launchers_import_and_publish_tools():
    from app.local_connect.mcp_server import mcp as legacy
    from app.paired_connect.server import mcp as paired
    for server in (legacy, paired):
        assert server is not None
        # Production's pinned major v2 must not accidentally import v1 FastMCP.
        if version("mcp").startswith("2."):
            assert type(server).__name__ == "MCPServer"


def test_unpaired_real_stdio_refuses_after_link(stdio_demo):
    env = {"MEASUREDFIELD_LOCAL_CONNECT_HOME": str(stdio_demo["home"])}

    async def verify(session):
        first = await session.call_tool("read_project_file", call_args(stdio_demo))
        assert not denied(first)
        assert "synthetic-stdio-sample" in str(first.content)
        (stdio_demo["home"] / "account_connection.json").write_text(
            json.dumps({"status": "revoked", "device_id": "synthetic"}),
            encoding="utf-8",
        )
        second = await session.call_tool("read_project_file", call_args(stdio_demo))
        assert denied(second)
        assert "synthetic-stdio-sample" not in str(second.content)
        edit = await session.call_tool(
            "propose_project_edit", {**call_args(stdio_demo), "new_content": "forbidden"}
        )
        assert denied(edit)
        assert stdio_demo["local"].pending() == []

    run_scenario(params("app.local_connect.mcp_server", env), verify)


def test_paired_real_stdio_revoke_terminates_existing_capability(stdio_demo):
    registry = PairingRegistry(stdio_demo["home"])
    pair = registry.create(
        "Synthetic paired", [stdio_demo["grant"]], allow_proposals=True,
        owner_confirmed=True, expires_minutes=10,
    )["pairing_id"]
    token = registry.token_for_local_launcher(pair)
    env = {
        "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(stdio_demo["home"]),
        "MEASUREDFIELD_PAIR_ID": pair,
        "MEASUREDFIELD_PAIR_SECRET": token,
    }

    async def verify(session):
        first = await session.call_tool("read_project_file", call_args(stdio_demo))
        assert not denied(first)
        assert "synthetic-stdio-sample" in str(first.content)
        assert registry.revoke(pair)
        later = await session.call_tool("read_project_file", call_args(stdio_demo))
        assert denied(later)
        assert "synthetic-stdio-sample" not in str(later.content)

    run_scenario(params("app.paired_connect.server", env), verify)


def test_paired_real_stdio_denies_unpaired_token(stdio_demo):
    env = {
        "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(stdio_demo["home"]),
        "MEASUREDFIELD_PAIR_ID": "",
        "MEASUREDFIELD_PAIR_SECRET": "",
    }

    async def verify(session):
        response = await session.call_tool("read_project_file", call_args(stdio_demo))
        assert denied(response)
        assert "synthetic-stdio-sample" not in str(response.content)

    run_scenario(params("app.paired_connect.server", env), verify)


def test_paired_real_stdio_fails_closed_after_new_account_state(stdio_demo):
    registry = PairingRegistry(stdio_demo["home"])
    pair = registry.create(
        "Synthetic paired", [stdio_demo["grant"]], allow_proposals=True,
        owner_confirmed=True, expires_minutes=10,
    )["pairing_id"]
    token = registry.token_for_local_launcher(pair)
    env = {
        "MEASUREDFIELD_LOCAL_CONNECT_HOME": str(stdio_demo["home"]),
        "MEASUREDFIELD_PAIR_ID": pair,
        "MEASUREDFIELD_PAIR_SECRET": token,
    }

    async def verify(session):
        first = await session.call_tool("read_project_file", call_args(stdio_demo))
        assert not denied(first)
        (stdio_demo["home"] / "account_connection.json").write_text(
            json.dumps({"status": "active", "device_id": "synthetic", "server_url": "https://workspace.invalid"}),
            encoding="utf-8",
        )
        after = await session.call_tool("read_project_file", call_args(stdio_demo))
        assert denied(after)
        assert "synthetic-stdio-sample" not in str(after.content)
        assert all(p["status"] != "active" for p in registry.list())

    run_scenario(params("app.paired_connect.server", env), verify)
