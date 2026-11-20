"""MF-051: no-secret offline ChatGPT launch prerequisites, NOT real OAuth tests."""
from __future__ import annotations

import json

import pytest

from scripts.chatgpt_connection_preflight import analyze_environment, main


def synthetic_staging():
    return {
        "MF_ENV": "staging",
        "DATABASE_URL": "postgresql://mf_runtime:synthetic-Safe-Password-5678@db.synthetic.net/mf",
        "MIGRATION_DATABASE_URL": "postgresql://mf_owner:synthetic-Migration-Password-4567@db.synthetic.net/mf",
        "MF_SECRET_KEY": "Synthetic-NotReal-32+Characters-For-Tests#7981",
        "MF_PUBLIC_BASE_URL": "https://app.synthetic.net",
        "MF_APP_HOSTS": "app.synthetic.net",
        "MF_SITES_BASE_DOMAIN": "sites.other-customer.net",
        "MF_TRUSTED_PROXY_HOPS": "1",
        "MF_EMAIL_PROVIDER": "none",
        "MF_PAYMENT_PROVIDER": "none",
        "MF_MCP_OAUTH_ISSUER": "https://auth.synthetic.net",
        "MF_MCP_OAUTH_JWKS_URL": "https://auth.synthetic.net/jwks.json",
        "MF_MCP_RESOURCE_URL": "https://connector.synthetic.net/mcp",
        "MF_OIDC_LINK_CLIENT_ID": "synthetic-unit-only-client",
        "MF_OIDC_LINK_CLIENT_SECRET": "synthetic-not-an-authentication-key",
        "MF_OIDC_LINK_AUTH_URL": "https://auth.synthetic.net/authorize",
        "MF_OIDC_LINK_TOKEN_URL": "https://auth.synthetic.net/token",
    }


def codes(report):
    return {item["key"]: item["code"] for item in report["blockers"]}


def test_missing_environment_fails_closed_and_does_not_claim_chatgpt():
    result = analyze_environment({})
    assert not result["configured"] and not result["ready_for_chatgpt"]
    assert result["status"] == "missing_configuration"
    for key in ("MF_ENV", "MF_PUBLIC_BASE_URL", "MF_MCP_OAUTH_ISSUER",
                "MF_OIDC_LINK_CLIENT_SECRET", "MF_MCP_RESOURCE_URL"):
        assert key in codes(result)
    assert result["external_calls_made"] == 0
    assert result["credentials_disclosed"] is False
    assert all(not value for key, value in result["checks"].items())


def test_complete_synthetic_config_cannot_claim_real_online_success():
    config = synthetic_staging()
    report = analyze_environment(config)
    assert report["configured"] is True, report["blockers"]
    assert report["status"] == "configuration_only_online_verification_required"
    assert not report["ready_for_chatgpt"]
    assert report["external_calls_made"] == 0
    assert report["manual_acceptance_gates"]
    assert all(g["status"] == "not_verified" for g in report["manual_acceptance_gates"])
    serialized = json.dumps(report)
    for key in ("MF_OIDC_LINK_CLIENT_SECRET", "MF_SECRET_KEY", "DATABASE_URL"):
        assert config[key] not in serialized
    assert "connector.synthetic.net" not in serialized
    assert "auth.synthetic.net" not in serialized


@pytest.mark.parametrize("key,value,expected", [
    ("MF_MCP_RESOURCE_URL", "http://connector.synthetic.net/mcp", "MCP_HTTPS_URL_INVALID"),
    ("MF_MCP_RESOURCE_URL", "https://connector.synthetic.net/other", "MCP_RESOURCE_PATH_INVALID"),
    ("MF_MCP_RESOURCE_URL", "https://connector.synthetic.net:444/mcp", "MCP_HTTPS_URL_INVALID"),
    ("MF_MCP_OAUTH_JWKS_URL", "https://unknown.synthetic.net/jwks", "MCP_ISSUER_MISMATCH"),
    ("MF_OIDC_LINK_AUTH_URL", "http://auth.synthetic.net/authorize", "OWNER_OIDC_ENDPOINT_INVALID"),
    ("MF_OIDC_LINK_TOKEN_URL", "https://wrong.synthetic.net/token", "OWNER_OIDC_ISSUER_MISMATCH"),
    ("MF_OIDC_LINK_TOKEN_URL", "https://auth.synthetic.net", "OWNER_OIDC_ENDPOINT_MISSING_PATH"),
    ("MF_OIDC_LINK_CLIENT_ID", "x" * 251, "OWNER_OIDC_CLIENT_INVALID"),
    ("MF_OIDC_LINK_CLIENT_SECRET", "X" * 4097, "OWNER_OIDC_SECRET_INVALID"),
    ("MF_PUBLIC_BASE_URL", "http://app.synthetic.net", "STAGING_INVALID"),
])
def test_all_host_and_oauth_misconfiguration_blocks(key, value, expected):
    env = synthetic_staging()
    env[key] = value
    report = analyze_environment(env)
    assert not report["configured"] and not report["ready_for_chatgpt"]
    assert any(x["key"] == key and x["code"] == expected for x in report["blockers"])


def test_secret_values_never_returned_even_when_bad():
    env = synthetic_staging()
    env["MF_OIDC_LINK_CLIENT_SECRET"] = "PRIVATE-DONT-LEAK-SECRET"
    env["MF_SECRET_KEY"] = "Another-PRIVATE-DONT-LEAK-SECRET"
    report = analyze_environment(env)
    text = json.dumps(report)
    assert "PRIVATE-DONT-LEAK" not in text
    assert "synthetic-Safe-Password" not in text


def test_cli_outputs_no_values_and_uses_nonzero_for_missing(monkeypatch, capsys):
    for key in synthetic_staging():
        monkeypatch.delenv(key, raising=False)
    code = main([])
    output = capsys.readouterr().out
    assert code == 2
    assert "CHATGPT_CONNECTOR_PREFLIGHT: missing_configuration" in output
    assert "MF_OIDC_LINK_CLIENT_SECRET" in output
    assert "ChatGPT plugin installed and real browser OAuth read verified: NO" in output


def test_cli_configured_only_exits_not_connected(monkeypatch, capsys):
    env = synthetic_staging()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    code = main(["--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["configured"] is True
    assert report["ready_for_chatgpt"] is False
    assert report["checks"]["chatgpt_real_read_tool_verified"] is False


def _synthetic_connections_page(*, available=False, connections=None):
    """Render the actual page template with synthetic users, never a live OAuth."""
    from pathlib import Path
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    repo = Path(__file__).resolve().parents[1]
    engine = Environment(
        loader=FileSystemLoader(str(repo / "app" / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    principal = SimpleNamespace(user=SimpleNamespace(
        is_platform_admin=False, name="Synthetic Owner", email="owner.invalid@example"
    ))
    return engine.get_template("app/mcp_connections.html").render(
        org=SimpleNamespace(name="Synthetic Local Business", status="active"),
        orgs=[],
        brand="MeasuredField",
        role="owner",
        principal=principal,
        csrf="SYNTHETIC-CSRF-ONLY",
        settings=SimpleNamespace(is_production=False),
        path="/app/connections",
        connections=connections or [],
        oidc_link_available=available,
        flash=None,
        flash_error=None,
    )


def test_owner_connection_journey_does_not_claim_live_chatgpt_without_link():
    page = _synthetic_connections_page()
    assert "Four steps to get connected" in page
    assert "Signed in" in page
    assert "Needs setup" in page
    assert "Not verified" in page
    assert "Not tested" in page
    assert "/static/mcp_connections.css" in page
    assert "No verified identity-provider accounts have been linked" in page
    assert 'action="/app/connections/start"' not in page
    assert "Open ChatGPT Plugins" in page
    assert "https://chatgpt.com/plugins" in page
    assert "Connecting never grants whole-drive access" in page
    assert "permission to send emails automatically" in page


def test_config_only_may_show_owner_oidc_button_but_not_plugin_install_success():
    page = _synthetic_connections_page(available=True)
    assert "Setup available" in page
    assert 'action="/app/connections/start"' in page
    assert "Verify and link my account" in page
    assert 'value="CONNECT READ ONLY"' in page
    assert "Not verified" in page
    assert "Not tested" in page
    assert "Your verified identity" in page
    assert "Identity linked" not in page


def test_real_link_display_still_does_not_claim_chatgpt_plugin_installed():
    link = {
        "id": "00000000-0000-4000-8000-000000000001",
        "provider": "idp.synthetic.example",
        "status": "connected",
        "linked_at": "2026-11-17T01:02:03Z",
        "disconnected_at": None,
    }
    page = _synthetic_connections_page(connections=[link])
    assert "Identity linked" in page
    assert "idp.synthetic.example" in page
    assert "Disconnect this account" in page
    assert 'value="DISCONNECT"' in page
    assert "Not verified" in page
    assert "Not tested" in page
    assert "Computer files are different" in page


def test_connection_provider_name_is_html_escaped_not_raw_injected():
    link = {
        "id": "00000000-0000-4000-8000-000000000001",
        "provider": "<script>synthetic-injected</script>",
        "status": "connected",
        "linked_at": "2026-11-17T01:02:03Z",
        "disconnected_at": None,
    }
    page = _synthetic_connections_page(connections=[link])
    assert "<script>synthetic-injected</script>" not in page
    assert "&lt;script&gt;synthetic-injected&lt;/script&gt;" in page


def test_connection_design_has_mobile_and_accessible_states():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    css = (repo / "app" / "static" / "mcp_connections.css").read_text(encoding="utf-8")
    assert ".mfcp-hero" in css
    assert ".mfcp-timeline" in css
    assert ".mfcp-table" in css
    assert "@media(max-width:620px)" in css
    assert "prefers-reduced-motion:reduce" in css
