"""MF-037 fail-closed OAuth resource server tests. Entirely synthetic, no network."""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.mcp_plugin.config import McpPluginSettings, McpPluginConfigurationError
from app.mcp_plugin.tokens import OAuthAccessDenied, OAuthJWTVerifier


ISSUER = "https://login.identity-fixture.test"
RESOURCE = "https://connector.identity-fixture.test/mcp"
SETTINGS = McpPluginSettings(
    issuer=ISSUER, jwks_url=ISSUER + "/.well-known/jwks.json",
    resource_url=RESOURCE)


@pytest.fixture
def keys():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _claims(**changes):
    current = int(time.time())
    claims = {
        "iss": ISSUER, "sub": "oidc|fixture-owner",
        "aud": RESOURCE, "iat": current - 5, "exp": current + 600,
        "scope": "mf:read", "client_id": "https://chatgpt.com/oauth/client.json",
        "resource": RESOURCE,
    }
    claims.update(changes)
    return claims


def _validator(key, resolver=None):
    client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key()))
    return OAuthJWTVerifier(
        SETTINGS, jwks_client=client, resolve_subject=resolver or (
            lambda issuer, sub, issued: uuid.UUID(int=1)))


def _token(key, claims=None, *, headers=None):
    return jwt.encode(
        claims if claims is not None else _claims(), key,
        algorithm="RS256", headers={"kid": "fixture-k1", **(headers or {})})


def test_signed_resource_bound_owner_link_token_accepted(keys):
    checks = []
    verifier = _validator(keys, resolver=lambda i, s, t: checks.append((i, s, t)) or uuid.UUID(int=1))
    verified = verifier.verify(_token(keys))
    assert verified.identity.issuer == ISSUER
    assert verified.identity.audience == RESOURCE
    assert verified.identity.subject == "oidc|fixture-owner"
    assert verified.identity.scopes == frozenset({"mf:read"})
    assert verified.client_id.startswith("https://chatgpt.com/oauth/")
    assert checks[0][0:2] == (ISSUER, "oidc|fixture-owner")


@pytest.mark.parametrize("change", [
    {"iss": "https://evil.example"},
    {"aud": "https://different.example/mcp"},
    {"scope": "mf:write"},
    {"scope": []},
    {"scope": {"unsafe": "mapping"}},
    {"sub": ""},
    {"client_id": ""},
    {"resource": "https://different.example/mcp"},
    {"exp": 1000000000},
    {"iat": 100000000000},
    {"nbf": 100000000000},
])
def test_wrong_jwt_claims_are_refused(keys, change):
    verifier = _validator(keys)
    with pytest.raises(OAuthAccessDenied):
        verifier.verify(_token(keys, _claims(**change)))


def test_forged_signature_or_unlinked_subject_is_refused(keys):
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OAuthAccessDenied):
        _validator(keys).verify(_token(forged))
    with pytest.raises(OAuthAccessDenied, match="linked"):
        _validator(keys, resolver=lambda *_: None).verify(_token(keys))


@pytest.mark.parametrize("header", [
    {"jku": "https://attacker.invalid/keys"},
    {"jwk": {"kty": "RSA"}},
    {"x5u": "https://attacker.invalid/keys"},
    {"kid": ""},
    {"kid": "K" * 300},
])
def test_jwt_header_remote_key_injection_is_refused(keys, header):
    with pytest.raises(OAuthAccessDenied):
        _validator(keys).verify(_token(keys, headers=header))


@pytest.mark.parametrize("raw", ["", "a.b.c", "x" * 9000, None, 1234])
def test_malformed_tokens_never_leak_information(keys, raw):
    with pytest.raises(OAuthAccessDenied) as exc:
        _validator(keys).verify(raw)
    assert "key" not in str(exc.value).lower()


def test_require_live_https_idp_and_plugin_resource(monkeypatch):
    for key in ("MF_MCP_OAUTH_ISSUER", "MF_MCP_OAUTH_JWKS_URL", "MF_MCP_RESOURCE_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(McpPluginConfigurationError, match="not configured"):
        McpPluginSettings.from_env()

    monkeypatch.setenv("MF_MCP_OAUTH_ISSUER", "https://auth.measuredfield.com")
    monkeypatch.setenv("MF_MCP_OAUTH_JWKS_URL", "https://auth.measuredfield.com/.well-known/jwks.json")
    monkeypatch.setenv("MF_MCP_RESOURCE_URL", "https://api.measuredfield.com/mcp")
    configured = McpPluginSettings.from_env()
    assert configured.resource_url.endswith("/mcp")
    assert configured.read_scope == "mf:read"

    for bad in ("http://api.measuredfield.com/mcp",
                "https://localhost/mcp",
                "https://127.0.0.1/mcp",
                "https://api.measuredfield.com/login",
                "https://api.measuredfield.com/mcp?token=secret",
                "https://user:password@api.measuredfield.com/mcp"):
        monkeypatch.setenv("MF_MCP_RESOURCE_URL", bad)
        with pytest.raises(McpPluginConfigurationError):
            McpPluginSettings.from_env()
    monkeypatch.setenv("MF_MCP_RESOURCE_URL", configured.resource_url)
    monkeypatch.setenv("MF_MCP_OAUTH_JWKS_URL", "https://unrelated.invalid/jwks")
    with pytest.raises(McpPluginConfigurationError):
        McpPluginSettings.from_env()


def test_plugin_no_anonymous_fallback_or_write_dispatch():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "mcp_plugin"
    server = (root / "server.py").read_text(encoding="utf-8")
    assert "get_access_token()" in server
    assert "ReadOnlyBusinessGateway" in server
    assert "ToolAnnotations(read_only_hint=True" in server
    for forbidden in ("send_email(", "publish_site(", "subprocess.run(",
                      "requests.post(", "httpx.post(", "OPENAI_API_KEY"):
        assert forbidden not in server
