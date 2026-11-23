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


def test_verify_require_read_scope_opt_out_preserves_security_checks(keys):
    checks = []
    verifier = _validator(keys, resolver=lambda i, s, t: checks.append((i, s, t)) or uuid.UUID(int=1))
    unscoped_token = _token(keys, _claims(scope=""))
    other_scope_token = _token(keys, _claims(scope="mf:drafts.write"))

    # Default verification MUST keep rejecting missing mf:read at tool boundaries.
    with pytest.raises(OAuthAccessDenied, match="read scope"):
        verifier.verify(unscoped_token)
    with pytest.raises(OAuthAccessDenied, match="read scope"):
        verifier.verify(other_scope_token)

    # Explicit opt-out accepts otherwise fully valid, linked signed JWTs without mf:read.
    verified_empty = verifier.verify(unscoped_token, require_read_scope=False)
    assert verified_empty.identity.issuer == ISSUER
    assert verified_empty.identity.audience == RESOURCE
    assert verified_empty.identity.subject == "oidc|fixture-owner"
    assert verified_empty.identity.scopes == frozenset()
    assert verified_empty.client_id == "https://chatgpt.com/oauth/client.json"
    assert checks[0][0:2] == (ISSUER, "oidc|fixture-owner")

    verified_other = verifier.verify(other_scope_token, require_read_scope=False)
    assert verified_other.identity.scopes == frozenset({"mf:drafts.write"})

    # Opt-out MUST NOT bypass signature, link, audience, issuer, resource, client or scope-format checks.
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OAuthAccessDenied):
        verifier.verify(_token(forged, _claims(scope="")), require_read_scope=False)
    with pytest.raises(OAuthAccessDenied, match="linked"):
        _validator(keys, resolver=lambda *_: None).verify(unscoped_token, require_read_scope=False)
    for bad_change in (
        {"scope": "", "aud": "https://different.example/mcp"},
        {"scope": "", "iss": "https://evil.example"},
        {"scope": "", "resource": "https://different.example/mcp"},
        {"scope": "", "client_id": ""},
        {"scope": {"unsafe": "mapping"}},
    ):
        with pytest.raises(OAuthAccessDenied):
            verifier.verify(_token(keys, _claims(**bad_change)), require_read_scope=False)


def test_only_linked_token_verifier_opts_out_of_read_scope():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "mcp_plugin" / "server.py").read_text(encoding="utf-8")
    assert source.count("require_read_scope") == 1
    module = ast.parse(source)
    factory = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "create_server")
    verifier_cls = next(n for n in factory.body if isinstance(n, ast.ClassDef) and n.name == "LinkedTokenVerifier")
    verify_token_fn = next(n for n in verifier_cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "verify_token")
    verifier_seg = ast.get_source_segment(source, verify_token_fn)
    assert "require_read_scope=False" in verifier_seg
    checked_fn = next(n for n in factory.body if isinstance(n, ast.FunctionDef) and n.name == "checked_bearer")
    checked_seg = ast.get_source_segment(source, checked_fn)
    assert "jwt_verifier.verify(verified.token)" in checked_seg
    assert "require_read_scope" not in checked_seg


def test_mcp_http_unscoped_linked_bearer_returns_403_insufficient_scope(keys):
    from importlib.metadata import version

    # Production requires MCP SDK v2. Main app suite may run under legacy v1;
    # the SDK v2 gate executes this real ASGI HTTP test end-to-end.
    if not version("mcp").startswith("2."):
        return

    from urllib.parse import urlsplit
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    from app.mcp_plugin.server import create_server

    resource_host = urlsplit(RESOURCE).hostname
    origin = f"https://{resource_host}"
    server = create_server(SETTINGS, token_verifier=_validator(keys))
    asgi_app = server.streamable_http_app(
        json_response=True,
        host=resource_host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[resource_host, f"{resource_host}:443"],
            allowed_origins=[origin, f"{origin}:443"],
        ),
    )
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "fixture", "version": "1.0"},
        },
    }
    base_headers = {"Accept": "application/json, text/event-stream"}
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with TestClient(asgi_app, base_url=origin) as client:
        # Anonymous request remains 401.
        anon = client.post("/mcp", json=payload, headers=base_headers)
        assert anon.status_code == 401

        # Invalid or forged bearer remains 401.
        invalid = client.post(
            "/mcp", json=payload,
            headers={**base_headers, "Authorization": "Bearer not-a-valid-jwt"},
        )
        assert invalid.status_code == 401

        forged_resp = client.post(
            "/mcp", json=payload,
            headers={**base_headers, "Authorization": f"Bearer {_token(forged, _claims(scope=''))}"},
        )
        assert forged_resp.status_code == 401

        # Valid, signed, linked bearer missing mf:read reaches SDK scope enforcement -> 403 insufficient_scope.
        unscoped_resp = client.post(
            "/mcp", json=payload,
            headers={**base_headers, "Authorization": f"Bearer {_token(keys, _claims(scope=''))}"},
        )
        assert unscoped_resp.status_code == 403
        www_auth = unscoped_resp.headers.get("www-authenticate", "")
        assert "insufficient_scope" in www_auth or "insufficient_scope" in unscoped_resp.text

    # Unlinked bearer without mf:read must still be rejected as 401, not 403.
    unlinked_server = create_server(
        SETTINGS, token_verifier=_validator(keys, resolver=lambda *_: None))
    unlinked_app = unlinked_server.streamable_http_app(
        json_response=True,
        host=resource_host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[resource_host, f"{resource_host}:443"],
            allowed_origins=[origin, f"{origin}:443"],
        ),
    )
    with TestClient(unlinked_app, base_url=origin) as client:
        unlinked_resp = client.post(
            "/mcp", json=payload,
            headers={**base_headers, "Authorization": f"Bearer {_token(keys, _claims(scope=''))}"},
        )
        assert unlinked_resp.status_code == 401


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

def test_chatgpt_can_discover_tools_before_oauth_but_cannot_call_them(keys):
    """Actual MCP2 ASGI transport: anonymous bootstrap only, protected business tools."""
    from importlib.metadata import version
    if not version("mcp").startswith("2."):
        return
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.testclient import TestClient
    from app.mcp_plugin.server import create_server
    from app.mcp_plugin.discovery import enable_anonymous_mcp_discovery
    from urllib.parse import urlsplit

    host = urlsplit(RESOURCE).hostname
    origin = f"https://{host}"
    server = create_server(SETTINGS, token_verifier=_validator(keys))
    app = enable_anonymous_mcp_discovery(server.streamable_http_app(
        json_response=True, host=host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, f"{host}:443"],
            allowed_origins=[origin, f"{origin}:443"],
        ),
    ))
    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {}, "clientInfo": {"name": "ChatGPT-probe", "version": "1"},
        },
    }
    common = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url=origin) as client:
        response = client.post("/mcp", json=init, headers=common)
        assert response.status_code == 200, response.text[:400]
        session_headers = {}
        if response.headers.get("mcp-session-id"):
            session_headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
        listing = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }, headers={**common, **session_headers})
        assert listing.status_code == 200, listing.text[:400]
        assert "list_my_businesses" in listing.text
        # No input, no alias, no schema trick can access actual tenant data.
        denied = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "list_my_businesses", "arguments": {}},
        }, headers={**common, **session_headers})
        assert denied.status_code == 401
        assert "www-authenticate" in denied.headers
        invalid = client.post("/mcp", json=init, headers={
            **common, "Authorization": "Bearer not-a-valid-jwt",
        })
        assert invalid.status_code == 401
        bogus = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 4, "method": "resources/read",
            "params": {"uri": "file:///D:/private"},
        }, headers=common)
        assert bogus.status_code == 401
        batch = client.post("/mcp", json=[
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "list_my_businesses", "arguments": {}}},
        ], headers=common)
        assert batch.status_code == 401
