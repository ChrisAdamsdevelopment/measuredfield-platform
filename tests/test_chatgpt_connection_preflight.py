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
