"""Actual official SDK2 tool callback tests for incremental computer Work consent.

Run with CHATGPT_CONNECTOR_WORKBENCH/.venv (MCP SDK 2.3.0) in addition to
the normal application test lane. All identities and devices are synthetic.
"""
from __future__ import annotations

import uuid
from importlib.metadata import version
from types import SimpleNamespace

import pytest

from app.mcp_connector.gateway import ConnectorAccessDenied, VerifiedOAuthIdentity
from app.mcp_plugin.config import McpPluginSettings
from app.mcp_plugin.tokens import OAuthAccessDenied, VerifiedBearer

ISSUER = "https://auth.synthetic.test/"
RESOURCE = "https://mcp.synthetic.test/mcp"
SETTINGS = McpPluginSettings(
    issuer=ISSUER, jwks_url=ISSUER + "jwks", resource_url=RESOURCE,
)


def _sdk2():
    # Production requires MCP v2. The basic suite also runs under legacy v1;
    # the separate v2 gate MUST execute these callbacks, not just import them.
    return version("mcp").startswith("2.")


class Verifier:
    def __init__(self, scopes, *, invalid=False):
        self.scopes = frozenset(scopes)
        self.invalid = invalid

    def verify(self, token):
        if self.invalid:
            raise OAuthAccessDenied("Invalid token")
        return VerifiedBearer(
            identity=VerifiedOAuthIdentity(
                issuer=ISSUER, subject="synthetic-owner", audience=RESOURCE,
                scopes=self.scopes,
            ),
            expires_at=2_000_000_000,
            issued_at=1_900_000_000,
            client_id="https://chatgpt.com/oauth/client.json",
        )


def _server(monkeypatch, *, scopes=("mf:read",), revoked=False, invalid=False):
    from mcp.server.auth.middleware import auth_context
    from app.mcp_plugin import server

    access = SimpleNamespace(
        subject="synthetic-owner", token="synthetic-signed-bearer",
        client_id="https://chatgpt.com/oauth/client.json",
        scopes=list(scopes), resource=RESOURCE,
    )
    monkeypatch.setattr(auth_context, "get_access_token", lambda: access)
    monkeypatch.setattr(
        server, "linked_subject_user_id",
        lambda *_: None if revoked else uuid.UUID(int=1),
    )
    return server.create_server(
        SETTINGS, gateway=object(), token_verifier=Verifier(scopes, invalid=invalid),
    )


@pytest.mark.parametrize("name,arguments", [
    ("queue_project_file_write", {
        "business_id": "synthetic-org", "device_id": "synthetic-device",
        "project_id": "x" * 24, "relative_path": "safe.txt",
        "content": "synthetic content", "request_id": "r" * 24,
    }),
    ("queue_project_directory", {
        "business_id": "synthetic-org", "device_id": "synthetic-device",
        "project_id": "x" * 24, "relative_path": "safe-folder",
        "request_id": "r" * 24,
    }),
    ("get_project_work_status", {
        "business_id": "synthetic-org", "job_id": "synthetic-job",
    }),
    ("cancel_project_work", {
        "business_id": "synthetic-org", "job_id": "synthetic-job",
    }),
])
def test_signed_read_token_prompts_for_computer_write_without_mutation(
    monkeypatch, name, arguments,
):
    if not _sdk2():
        return
    from app.services import device_work

    def forbidden(*args, **kwargs):
        raise AssertionError("Unconsented device action was executed")

    for action in ("enqueue", "status", "cancel"):
        monkeypatch.setattr(device_work, action, forbidden)
    mcp = _server(monkeypatch)
    result = mcp._tool_manager.get_tool(name).fn(**arguments)
    data = result.model_dump(by_alias=True, exclude_none=True)
    assert data["isError"] is True
    assert 'scope="mf:read mf:computer.write"' in (
        data["_meta"]["mcp/www_authenticate"][0]
    )


@pytest.mark.parametrize("revoked,invalid", [(True, False), (False, True)])
def test_revoked_or_invalid_read_token_gets_no_consent_prompt(
    monkeypatch, revoked, invalid,
):
    if not _sdk2():
        return
    mcp = _server(monkeypatch, revoked=revoked, invalid=invalid)
    fn = mcp._tool_manager.get_tool("queue_project_directory").fn
    with pytest.raises(ConnectorAccessDenied):
        fn("synthetic-org", "synthetic-device", "x" * 24, "safe", "r" * 24)


def test_write_scope_dispatches_only_after_valid_link(monkeypatch):
    if not _sdk2():
        return
    from app.services import device_work

    called = []
    monkeypatch.setattr(
        device_work, "enqueue",
        lambda bearer, **kwargs: called.append((bearer, kwargs)) or {"status": "queued"},
    )
    mcp = _server(monkeypatch, scopes=("mf:read", "mf:computer.write"))
    fn = mcp._tool_manager.get_tool("queue_project_directory").fn
    assert fn("synthetic-org", "synthetic-device", "x" * 24, "safe", "r" * 24) == {
        "status": "queued",
    }
    assert len(called) == 1
    assert called[0][1]["action"] == "make_directory"
