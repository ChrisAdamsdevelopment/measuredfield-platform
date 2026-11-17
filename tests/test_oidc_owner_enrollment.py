"""MF-041: OIDC issuer-bound, signed and nonce-verified account enrollment.

Synthetic identity provider. No network, customer data, or OAuth credentials.
"""
from __future__ import annotations

import hashlib
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import mcp_oidc_enrollment as oidc


ISSUER = "https://identity.example.org"
CONFIG = oidc.OIDCOwnerSettings(
    issuer=ISSUER, jwks_url=ISSUER + "/.well-known/jwks.json",
    authorization_endpoint=ISSUER + "/authorize",
    token_endpoint=ISSUER + "/oauth/token",
    client_id="fixture-owner-link-client",
    client_secret="synthetic-client-secret-for-offline-tests",
    redirect_uri="https://app.example.org/app/connections/callback",
)


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _claims(issued_nonce, **changes):
    now = int(time.time())
    data = {
        "iss": ISSUER, "sub": "oidc|actual-owner-4242",
        "aud": CONFIG.client_id, "nonce": issued_nonce,
        "iat": now - 1, "exp": now + 600,
    }
    data.update(changes)
    return data


def _verify(key, nonce, *, changes=None, signer=None, headers=None,
            status=200, content_type="application/json"):
    data = _claims(nonce, **(changes or {}))
    token = jwt.encode(data, signer or key, algorithm="RS256",
                       headers={"kid": "offline-idp-key", **(headers or {})})
    response = httpx.Response(status, json={"id_token": token},
                              headers={"content-type": content_type})
    return oidc.exchange_and_verify(
        "offline-authorize-code", b"offline-verifier-value",
        hashlib.sha256(nonce.encode("ascii")).hexdigest(), CONFIG,
        token_transport=lambda *_: response,
        jwks_client=SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
        ))


def test_valid_signed_oidc_id_token_is_a_subject_not_email(key):
    assert _verify(key, "randomized-owner-nonce") == "oidc|actual-owner-4242"


@pytest.mark.parametrize("claims", [
    {"iss": "https://evil.example.org"},
    {"aud": "another-client"},
    {"aud": ["fixture-owner-link-client", "different-app"]},
    {"exp": int(time.time()) - 900},
    {"iat": int(time.time()) + 3600},
    {"sub": ""},
    {"sub": "x" * 300},
    {"azp": "another-client"},
    {"nonce": "attacker-guess"},
])
def test_id_token_wrong_claims_rejected(key, claims):
    with pytest.raises(oidc.OwnerEnrollmentDenied):
        _verify(key, "actual-expected-nonce", changes=claims)


def test_forged_id_token_and_attacker_key_urls_rejected(key):
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(oidc.OwnerEnrollmentDenied):
        _verify(key, "nonce", signer=forged)
    for header in ({"jku": "https://evil.example.org/key.json"},
                   {"jwk": {"kty": "RSA"}}, {"kid": ""}, {"kid": "x" * 256}):
        with pytest.raises(oidc.OwnerEnrollmentDenied):
            _verify(key, "nonce", headers=header)


def test_bad_token_exchange_and_non_json_provider_response_rejected(key):
    for status, ct in ((401, "application/json"), (200, "text/html")):
        with pytest.raises(oidc.OwnerEnrollmentDenied):
            _verify(key, "nonce", status=status, content_type=ct)


def test_start_authorization_is_provider_pinned_with_pkce_and_no_email(monkeypatch):
    class MemoryDB:
        def __init__(self):
            self.added = []
        def add(self, x):
            self.added.append(x)
    monkeypatch.setattr(oidc, "_cipher", lambda: SimpleNamespace(
        encrypt=lambda value: b"encrypted-challenge-for-unit-test"))
    db = MemoryDB()
    result = oidc.start_owner_link(db, user_id="server-trusted-user",
                                   session_id="server-trusted-session", settings=CONFIG)
    params = parse_qs(urlsplit(result.authorize_url).query)
    assert result.authorize_url.startswith(ISSUER + "/authorize?")
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["client_id"] == [CONFIG.client_id]
    assert params["redirect_uri"] == [CONFIG.redirect_uri]
    assert params["scope"] == ["openid profile"]
    assert params["state"] == [result.state]
    assert params["nonce"] == [result.nonce]
    assert "email" not in params and "user_id" not in params and "subject" not in params
    assert "code_verifier" not in params and "client_secret" not in params
    challenge = db.added[0]
    assert challenge.state_hash == hashlib.sha256(result.state.encode("ascii")).hexdigest()
    assert challenge.nonce_hash == hashlib.sha256(result.nonce.encode("ascii")).hexdigest()
    assert challenge.pkce_ciphertext != result.state.encode()
    assert challenge.pkce_ciphertext != result.nonce.encode()


@pytest.mark.parametrize("code", [None, "", "short", "c" * 3000])
def test_untrusted_provider_code_rejected_without_network(code):
    with pytest.raises(oidc.OwnerEnrollmentDenied):
        oidc.exchange_and_verify(code, b"verifier", "x" * 64, CONFIG,
                                 token_transport=lambda *_: (_ for _ in ()).throw(
                                     AssertionError("token exchange must not be attempted")))


def test_missing_external_provider_never_enables_linking(monkeypatch):
    for name in ("MF_MCP_OAUTH_ISSUER", "MF_MCP_OAUTH_JWKS_URL",
                 "MF_MCP_RESOURCE_URL", "MF_OIDC_LINK_AUTH_URL",
                 "MF_OIDC_LINK_TOKEN_URL", "MF_OIDC_LINK_CLIENT_ID",
                 "MF_OIDC_LINK_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert not oidc.config_available()
    with pytest.raises(oidc.OwnerEnrollmentUnavailable):
        oidc.OIDCOwnerSettings.from_env()
