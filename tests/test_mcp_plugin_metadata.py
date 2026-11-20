"""MF-039: ChatGPT OAuth tool metadata and stable account identity contract.

These pure tests run in the main application environment even when the SDK v2
is only installed in the isolated connector test venv. In addition, the local
SDK v2.3.0 scenario verifies the *actual* registered Tool._meta objects.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.mcp_plugin.server import oauth_tool_metadata


@pytest.mark.parametrize("scope", ["mf:read", "mf:custom:read"])
def test_oauth_meta_scope_exposed_as_explicit_required_scheme(scope):
    metadata = oauth_tool_metadata(scope)
    assert metadata == {
        "securitySchemes": [{"type": "oauth2", "scopes": [scope]}]
    }
    assert "noauth" not in repr(metadata).lower()
    assert "openai/profile" not in metadata


def test_profile_marked_for_chatgpt_multi_account_identity():
    metadata = oauth_tool_metadata("mf:read", profile=True)
    assert metadata["openai/profile"] is True
    assert metadata["securitySchemes"] == [{"type": "oauth2", "scopes": ["mf:read"]}]


@pytest.mark.parametrize("invalid", ["", "   ", None, False, 0, [], {}])
def test_missing_or_malformed_oauth_scope_fail_closed(invalid):
    with pytest.raises(ValueError, match="nonblank"):
        oauth_tool_metadata(invalid)


def _tools_in_server_source():
    source = Path(__file__).resolve().parents[1] / "app" / "mcp_plugin" / "server.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    for top in module.body:
        if not isinstance(top, ast.FunctionDef) or top.name != "create_server":
            continue
        for node in top.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = [
                dec for dec in node.decorator_list
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "tool" and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "mcp"
            ]
            if decorators:
                yield node.name, decorators[0]


def test_all_private_tools_have_explicit_oauth_metadata():
    tools = dict(_tools_in_server_source())
    assert set(tools) == {
        "get_connected_profile", "list_my_businesses",
        "get_business_overview", "list_inquiries", "get_inquiry", "preview_reply",
        "list_supported_census_markets", "get_census_market_snapshot",
        "save_inquiry_reply_draft", "queue_project_file_write",
        "queue_project_directory", "get_project_work_status", "cancel_project_work",
        "list_website_work_requests", "apply_website_work_request",
    }
    for name, decorator in tools.items():
        kwargs = {item.arg: item.value for item in decorator.keywords}
        assert "annotations" in kwargs
        assert "meta" in kwargs, f"{name} omitted explicit ChatGPT OAuth metadata"
        call = kwargs["meta"]
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Name)
        assert call.func.id == "oauth_tool_metadata"
        assert len(call.args) == 1
        assert isinstance(call.args[0], ast.Attribute)
        assert call.args[0].attr == "read_scope"
        extra_scope = {k.arg: k.value for k in call.keywords}.get("with_scope")
        if name == "save_inquiry_reply_draft":
            assert isinstance(extra_scope, ast.Attribute)
            assert extra_scope.attr == "write_scope"
        elif name == "apply_website_work_request":
            assert isinstance(extra_scope, ast.Attribute)
            assert extra_scope.attr == "WRITE_SCOPE"
            assert isinstance(extra_scope.value, ast.Name)
            assert extra_scope.value.id == "website_work"
        elif name in ("queue_project_file_write", "queue_project_directory",
                      "get_project_work_status", "cancel_project_work"):
            assert isinstance(extra_scope, ast.Attribute)
            assert extra_scope.attr == "WORK_SCOPE"
            assert isinstance(extra_scope.value, ast.Name)
            assert extra_scope.value.id == "device_work"
        else:
            assert extra_scope is None


def test_only_authenticated_profile_is_marked_as_special_identity():
    tools = dict(_tools_in_server_source())
    for name, decorator in tools.items():
        kwargs = {item.arg: item.value for item in decorator.keywords}
        meta = kwargs["meta"]
        flag = {
            item.arg: item.value for item in meta.keywords
        }.get("profile")
        if name == "get_connected_profile":
            assert isinstance(flag, ast.Constant) and flag.value is True
        else:
            assert flag is None


def test_profile_is_derived_from_verified_subject_not_tool_parameters():
    profile_node = next(
        node for top in ast.parse((
            Path(__file__).resolve().parents[1] / "app" / "mcp_plugin" / "server.py"
        ).read_text(encoding="utf-8")).body
        if isinstance(top, ast.FunctionDef) and top.name == "create_server"
        for node in top.body if isinstance(node, ast.FunctionDef)
        and node.name == "get_connected_profile"
    )
    assert profile_node.args.args == []
    names_called = {
        n.func.id for n in ast.walk(profile_node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "checked_bearer" in names_called
    assert "linked_subject_user_id" in names_called
    assert "uuid4" not in names_called
    assert "send_email" not in names_called
