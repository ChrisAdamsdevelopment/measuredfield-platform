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


def test_incremental_website_work_scope_challenge_has_no_side_effects():
    from app.mcp_plugin.server import oauth_additional_scope_challenge

    settings = McpPluginSettings(
        issuer="https://auth.synthetic.test/",
        jwks_url="https://auth.synthetic.test/jwks",
        resource_url="https://mcp.synthetic.test/mcp",
    )
    result = oauth_additional_scope_challenge(settings, scope="mf:website.write")
    data = result.model_dump(by_alias=True, exclude_none=True)
    assert data["isError"] is True
    challenge = data["_meta"]["mcp/www_authenticate"][0]
    assert 'scope="mf:read mf:website.write"' in challenge
    assert 'error="insufficient_scope"' in challenge
    assert "mcp.synthetic.test/.well-known/oauth-protected-resource/mcp" in challenge
    assert "Nothing was changed or published" in data["content"][0]["text"]
    assert all("token=" not in str(row) for row in data["content"])
    with pytest.raises(ValueError):
        oauth_additional_scope_challenge(settings, scope="mf:read")
    with pytest.raises(ValueError):
        oauth_additional_scope_challenge(settings, scope="other:scope")


def test_website_work_tool_requires_verified_link_before_incremental_scope_prompt():
    source = (Path(__file__).resolve().parents[1] / "app" /
              "mcp_plugin" / "server.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    factory = next(node for node in module.body
                   if isinstance(node, ast.FunctionDef) and node.name == "create_server")
    tool = next(node for node in factory.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "apply_website_work_request")
    segment = ast.get_source_segment(source, tool)
    assert "checked_bearer(extra_scope=website_work.WRITE_SCOPE)" in segment
    assert "read_bearer = checked_bearer()" in segment
    assert "linked_subject_user_id(" in segment
    assert "oauth_additional_scope_challenge(" in segment
    assert segment.index("linked_subject_user_id(") < segment.index("return oauth_additional_scope_challenge(")
    assert "website_work.apply_for_oauth(" in segment


@pytest.mark.parametrize("work_scope", ["mf:computer.write", "mf:website.write"])
def test_incremental_work_scope_challenge_is_explicit_and_non_executing(work_scope):
    from app.mcp_plugin.server import oauth_additional_scope_challenge

    settings = McpPluginSettings(
        issuer="https://auth.synthetic.test/",
        jwks_url="https://auth.synthetic.test/jwks",
        resource_url="https://mcp.synthetic.test/mcp",
    )
    result = oauth_additional_scope_challenge(settings, scope=work_scope)
    data = result.model_dump(by_alias=True, exclude_none=True)
    challenge = data["_meta"]["mcp/www_authenticate"][0]
    assert data["isError"] is True
    assert f'scope="mf:read {work_scope}"' in challenge
    assert 'error="insufficient_scope"' in challenge
    assert "resource_metadata=" in challenge
    assert "Nothing was changed or published" in data["content"][0]["text"]
    assert "token=" not in str(data)


def test_computer_write_tools_trigger_challenge_only_after_current_link_check():
    source = (Path(__file__).resolve().parents[1] / "app" /
              "mcp_plugin" / "server.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    factory = next(n for n in module.body
                   if isinstance(n, ast.FunctionDef) and n.name == "create_server")
    funcs = {n.name: n for n in factory.body if isinstance(n, ast.FunctionDef)}
    helper = ast.get_source_segment(source, funcs["computer_work_bearer"])
    assert "checked_bearer(extra_scope=device_work.WORK_SCOPE)" in helper
    assert "read_bearer = checked_bearer()" in helper
    assert "linked_subject_user_id(" in helper
    assert "oauth_additional_scope_challenge(" in helper
    assert helper.index("linked_subject_user_id(") < helper.index(
        "return None, oauth_additional_scope_challenge(")
    for name, downstream in (
        ("queue_project_file_write", "device_work.enqueue("),
        ("queue_project_directory", "device_work.enqueue("),
        ("get_project_work_status", "device_work.status("),
        ("cancel_project_work", "device_work.cancel("),
    ):
        segment = ast.get_source_segment(source, funcs[name])
        assert "bearer, challenge = computer_work_bearer()" in segment
        assert "if challenge is not None:" in segment
        assert "return challenge" in segment
        assert segment.index("return challenge") < segment.index(downstream)
