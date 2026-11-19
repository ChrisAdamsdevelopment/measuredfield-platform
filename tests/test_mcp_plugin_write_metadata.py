"""MCP write declaration: an extra explicit OAuth scope and honest annotations."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.mcp_plugin.server import oauth_tool_metadata
from app.mcp_plugin.config import McpPluginSettings


def test_read_scope_does_not_silently_grant_write():
    assert oauth_tool_metadata("mf:read") == {
        "securitySchemes": [{"type": "oauth2", "scopes": ["mf:read"]}]}
    assert oauth_tool_metadata("mf:read", with_scope="mf:drafts.write") == {
        "securitySchemes": [{"type": "oauth2", "scopes": ["mf:read", "mf:drafts.write"]}]}
    settings = McpPluginSettings(
        issuer="https://auth.synthetic.test/", jwks_url="https://auth.synthetic.test/jwks",
        resource_url="https://mcp.synthetic.test/mcp")
    assert settings.write_scope != settings.read_scope
    assert settings.write_scope == "mf:drafts.write"


@pytest.mark.parametrize("bad", ["", "  ", False, 1, "mf:read"])
def test_bad_write_scope_metadata_is_rejected(bad):
    with pytest.raises(ValueError):
        oauth_tool_metadata("mf:read", with_scope=bad)


def test_write_tool_is_not_mislabelled_as_read_only():
    source = Path(__file__).resolve().parents[1] / "app" / "mcp_plugin" / "server.py"
    ast_module = ast.parse(source.read_text(encoding="utf-8"))
    create = next(n for n in ast_module.body if isinstance(n, ast.FunctionDef)
                  and n.name == "create_server")
    node = next(n for n in create.body if isinstance(n, ast.FunctionDef)
                and n.name == "save_inquiry_reply_draft")
    ann = next(
        k.value for dec in node.decorator_list if isinstance(dec, ast.Call)
        for k in dec.keywords if k.arg == "annotations"
    )
    assert isinstance(ann, ast.Call)
    props = {k.arg: k.value for k in ann.keywords}
    for field, expected in (("read_only_hint", False), ("destructive_hint", False),
                            ("idempotent_hint", True), ("open_world_hint", False)):
        assert isinstance(props[field], ast.Constant)
        assert props[field].value is expected
    metadata = next(
        k.value for dec in node.decorator_list if isinstance(dec, ast.Call)
        for k in dec.keywords if k.arg == "meta"
    )
    keys = {k.arg: k.value for k in metadata.keywords}
    assert "with_scope" in keys
    assert isinstance(keys["with_scope"], ast.Attribute)
    assert keys["with_scope"].attr == "write_scope"
    assert "checked_bearer(require_write=True)" in ast.get_source_segment(
        source.read_text(encoding="utf-8"), node)


def test_missing_write_consent_emits_mcp_challenge_without_tool_execution():
    from app.mcp_plugin.server import oauth_write_scope_challenge

    settings = McpPluginSettings(
        issuer="https://auth.synthetic.test/", jwks_url="https://auth.synthetic.test/jwks",
        resource_url="https://mcp.synthetic.test/mcp")
    result = oauth_write_scope_challenge(settings)
    encoded = result.model_dump(by_alias=True, exclude_none=True)
    assert encoded["isError"] is True
    assert "mcp/www_authenticate" in encoded["_meta"]
    challenge = encoded["_meta"]["mcp/www_authenticate"][0]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="mf:read mf:drafts.write"' in challenge
    assert 'https://mcp.synthetic.test/.well-known/oauth-protected-resource/mcp' in challenge
    assert all("token=" not in str(item) for item in encoded["content"])



def test_revoked_scope_challenge_path_checks_current_owner_link():
    """The runtime MCPv2 dispatch is exercised by isolated official SDK script."""
    import ast
    source = (Path(__file__).resolve().parents[1] / 'app' /
              'mcp_plugin' / 'server.py').read_text(encoding='utf8')
    module = ast.parse(source)
    factory = next(n for n in module.body
                   if isinstance(n, ast.FunctionDef) and n.name == 'create_server')
    tool = next(n for n in factory.body
                if isinstance(n, ast.FunctionDef)
                and n.name == 'save_inquiry_reply_draft')
    calls = [n.func.id for n in ast.walk(tool)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert 'checked_bearer' in calls
    assert 'linked_subject_user_id' in calls
    assert 'oauth_write_scope_challenge' in calls
    segment = ast.get_source_segment(source, tool)
    assert segment.index('linked_subject_user_id(') < segment.index(
        'return oauth_write_scope_challenge(settings)')
