"""MF-013 OAuth resource JWT proof: synthetic keys, no network, no user tokens."""
from __future__ import annotations
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.mcp_auth.identity import OAuthAccessDenied, OAuthConfig, OAuthJWTVerifier

ISSUER = "https://login.test.mf.example/"
RESOURCE = "https://mcp.test.mf.example/mcp"
JWKS = "https://login.test.mf.example/.well-known/jwks.json"


@pytest.fixture()
def auth_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key()
    config = OAuthConfig(issuer=ISSUER, resource_url=RESOURCE, jwks_url=JWKS)
    keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public))
    return private, config, keys


def _mint(key, **overrides):
    claims = {"iss": ISSUER, "sub": "externally-linked-123",
              "aud": RESOURCE, "iat": int(time.time()) - 10,
              "exp": int(time.time()) + 900,
              "scope": "mf:read", "jti": "one-token-uuid",
              "azp": "https://chatgpt.com/oauth/client.json"}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def test_signed_token_is_only_accepted_with_verified_claims_and_revocation(auth_pair):
    key, cfg, jwks = auth_pair
    verifier = OAuthJWTVerifier(cfg, jwks_client=jwks, is_revoked=lambda iss, sub, jti: False)
    result = verifier.verify(_mint(key))
    assert result.identity.issuer == ISSUER
    assert result.identity.audience == RESOURCE
    assert result.identity.subject == "externally-linked-123"
    assert result.identity.scopes == frozenset({"mf:read"})
    assert result.client_id.startswith("https://")
    assert result.expires_at > int(time.time())
    assert result.jti == "one-token-uuid"


@pytest.mark.parametrize("override", [
    {"aud": "https://another.test.example/mcp"},
    {"iss": "https://evil.test.example"},
    {"scope": "mf:readonly"},
    {"scope": ["notmf:read"]},
    {"scope": "files:read"},
    {"exp": 1},
    {"iat": int(time.time()) + 3600},
    {"sub": ""},
    {"jti": ""},
    {"jti": None},
    {"azp": None},
    {"scope": {"mf:read": True}},
    {"aud": [RESOURCE, "https://another.test.example/mcp"]},
])
def test_untrusted_or_incomplete_claims_are_denied(auth_pair, override):
    key, cfg, jwks = auth_pair
    checker = OAuthJWTVerifier(cfg, jwks_client=jwks, is_revoked=lambda *args: False)
    with pytest.raises(OAuthAccessDenied):
        checker.verify(_mint(key, **override))


def test_jwks_signature_must_match(auth_pair):
    key, cfg, jwks = auth_pair
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    checker = OAuthJWTVerifier(cfg, jwks_client=jwks, is_revoked=lambda *args: False)
    with pytest.raises(OAuthAccessDenied):
        checker.verify(_mint(wrong))


def test_token_revocation_is_explicit_and_fail_closed(auth_pair):
    key, cfg, jwks = auth_pair
    for revoked in (None, lambda *args: True):
        with pytest.raises(OAuthAccessDenied):
            OAuthJWTVerifier(cfg, jwks_client=jwks, is_revoked=revoked).verify(_mint(key))
    def database_down(*_):
        raise RuntimeError("revocation DB unavailable")
    with pytest.raises(OAuthAccessDenied):
        OAuthJWTVerifier(cfg, jwks_client=jwks, is_revoked=database_down).verify(_mint(key))


def test_no_dynamic_jwks_urls_accepted_from_client_token(auth_pair):
    key, cfg, jwks = auth_pair
    token = jwt.encode(
        jwt.decode(_mint(key), options={"verify_signature": False}),
        key, algorithm="RS256",
        headers={"kid": "test-key", "jku": "https://evil.example/keys"})
    checker = OAuthJWTVerifier(cfg, jwks_client=jwks, is_revoked=lambda *args: False)
    with pytest.raises(OAuthAccessDenied):
        checker.verify(token)
    with pytest.raises(OAuthAccessDenied):
        checker.verify("this-is-not-a-jwt")
    with pytest.raises(OAuthAccessDenied):
        checker.verify({"token": _mint(key)})


def test_configuration_needs_real_https_resource_and_jwks(monkeypatch):
    for value in ("http://login.example", "https://localhost/mcp",
                  "https://user:password@auth.example/mcp", "https://foo.example/mcp?secret=1"):
        with pytest.raises(ValueError):
            OAuthConfig(issuer=value, jwks_url=JWKS, resource_url=RESOURCE)
    with pytest.raises(ValueError):
        OAuthConfig(issuer=ISSUER, jwks_url=JWKS, resource_url="https://resource.example/not-mcp")
    for env in ("MF_MCP_ISSUER", "MF_MCP_JWKS_URL", "MF_MCP_RESOURCE_URL"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(ValueError):
        OAuthConfig.from_env()
