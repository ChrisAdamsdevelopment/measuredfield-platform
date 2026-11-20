"""MF-041 real-PostgreSQL owner OIDC account linking and replay isolation.

Synthetic JWT provider only, no external identity registration or network.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.db import session_scope
from app.models import McpIdentityLink, McpOwnerOIDCChallenge, Membership, now
from app.services import mcp_oidc_enrollment as oidc
from .conftest import client_for, csrf_of, onboard_business

ISSUER = "https://synthetic.example.org"
CONFIG = oidc.OIDCOwnerSettings(
    issuer=ISSUER,
    jwks_url=ISSUER + "/.well-known/jwks.json",
    authorization_endpoint=ISSUER + "/authorize",
    token_endpoint=ISSUER + "/oauth/token",
    client_id="measuredfield-owner-oidc-synthetic-client",
    client_secret="fictional-only-offline-integration-client-secret",
    redirect_uri="https://app.example.org/app/connections/callback",
)
CODE = "synthetic-authorization-code-only"


@pytest.fixture
def synthetic_idp(monkeypatch):
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(oidc.OIDCOwnerSettings, "from_env", classmethod(lambda cls: CONFIG))
    real_verify = oidc.exchange_and_verify
    state = {"nonce": None, "subject": "provider|" + secrets.token_hex(12), "claims": {},
             "exchanges": 0, "tampered": False}

    def verify_code(code, verifier, nonce_hash, settings):
        state["exchanges"] += 1
        if code != CODE:
            raise oidc.OwnerEnrollmentDenied("Synthetic provider refused code.")
        now_epoch = int(time.time())
        claims = {
            "iss": ISSUER, "aud": CONFIG.client_id, "sub": state["subject"],
            "iat": now_epoch, "exp": now_epoch + 600,
            "nonce": state["nonce"],
            **state["claims"],
        }
        selected_signer = (rsa.generate_private_key(public_exponent=65537, key_size=2048)
                           if state["tampered"] else signing_key)
        id_token = jwt.encode(claims, selected_signer, algorithm="RS256",
                              headers={"kid": "offline-signature"})
        resp = httpx.Response(200, json={"id_token": id_token})
        return real_verify(
            code, verifier, nonce_hash, settings,
            token_transport=lambda *_: resp,
            jwks_client=SimpleNamespace(
                get_signing_key_from_jwt=lambda _: SimpleNamespace(key=signing_key.public_key())))
    monkeypatch.setattr(oidc, "exchange_and_verify", verify_code)
    return state


def _owner(app, label):
    client, org_id, _ = onboard_business(
        app, business="Link account " + label,
        owner_email="oidc-" + label + "@fictional.example")
    with session_scope(org_id=org_id) as db:
        member = db.scalar(select(Membership).where(
            Membership.org_id == org_id, Membership.role == "owner"))
        user_id = member.user_id
    return client, user_id


def _start(client, idp, csrf=True):
    reply = client.post("/app/connections/start",
                        data={"csrf": csrf_of(client) if csrf else "",
                              "confirm": "CONNECT READ ONLY"})
    assert reply.status_code == 303, reply.text[:180]
    location = reply.headers["location"]
    assert location.startswith(ISSUER + "/authorize?"), location[:100]
    params = parse_qs(urlsplit(location).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == [CONFIG.redirect_uri]
    assert params["scope"] == ["openid profile"]
    idp["nonce"] = params["nonce"][0]
    return params["state"][0]


def _callback(client, state, **extra):
    params = {"state": state, "code": CODE, **extra}
    return client.get("/app/connections/callback", params=params)


def test_session_bound_oidc_flow_creates_verified_link_once(platform, synthetic_idp):
    owner, uid = _owner(platform, "success")
    assert "Verify and link my account" in owner.get("/app/connections").text
    state = _start(owner, synthetic_idp)
    with session_scope() as db:
        row = db.scalar(select(McpOwnerOIDCChallenge).where(
            McpOwnerOIDCChallenge.state_hash == hashlib.sha256(state.encode()).hexdigest()))
        assert row is not None and row.user_id == uid
        assert row.session_id is not None and row.consumed_at is None
        assert state.encode() not in row.pkce_ciphertext
        assert synthetic_idp["nonce"].encode() not in row.pkce_ciphertext
        assert len(row.pkce_ciphertext) > 50
    first = _callback(owner, state)
    assert first.status_code == 303
    assert first.headers["location"].startswith("/app/connections")
    entries = owner.get("/api/v1/mcp/account-links").json()["connections"]
    assert len(entries) == 1 and entries[0]["status"] == "connected"
    with session_scope() as db:
        link = db.scalar(select(McpIdentityLink).where(
            McpIdentityLink.issuer == ISSUER,
            McpIdentityLink.subject == synthetic_idp["subject"]))
        assert link is not None and link.user_id == uid
        challenge = db.scalar(select(McpOwnerOIDCChallenge).where(
            McpOwnerOIDCChallenge.state_hash == hashlib.sha256(state.encode()).hexdigest()))
        assert challenge.consumed_at is not None
        assert challenge.pkce_ciphertext == b"consumed"
    again = _callback(owner, state)
    assert again.status_code == 303
    assert synthetic_idp["exchanges"] == 1


def test_untrusted_user_and_session_cannot_consume_someone_elses_state(platform, synthetic_idp):
    alice, alice_uid = _owner(platform, "state-alice")
    bob, _ = _owner(platform, "state-bob")
    state = _start(alice, synthetic_idp)
    forged = _callback(bob, state)
    assert forged.status_code == 303
    assert synthetic_idp["exchanges"] == 0
    assert bob.get("/api/v1/mcp/account-links").json()["connections"] == []
    assert _callback(client_for(platform), state).status_code == 303
    legitimate = _callback(alice, state)
    assert legitimate.status_code == 303
    with session_scope() as db:
        row = db.scalar(select(McpIdentityLink).where(
            McpIdentityLink.issuer == ISSUER,
            McpIdentityLink.subject == synthetic_idp["subject"]))
        assert row is not None and row.user_id == alice_uid


def test_missing_csrf_and_consent_never_start_provider_flow(platform, synthetic_idp):
    alice, alice_uid = _owner(platform, "csrf")
    no_token = alice.post("/app/connections/start", data={"confirm": "CONNECT READ ONLY"})
    assert no_token.status_code == 403
    refuse = alice.post("/app/connections/start",
                        data={"csrf": csrf_of(alice), "confirm": "not-approved"})
    assert refuse.status_code == 303
    with session_scope() as db:
        assert db.scalar(select(McpOwnerOIDCChallenge).where(
            McpOwnerOIDCChallenge.user_id == alice_uid)) is None


@pytest.mark.parametrize("tamper", ["nonce", "signature", "audience", "issuer"])
def test_invalid_provider_id_token_never_creates_link(platform, synthetic_idp, tamper):
    owner, _ = _owner(platform, "tamper-" + tamper)
    state = _start(owner, synthetic_idp)
    if tamper == "nonce":
        synthetic_idp["claims"] = {"nonce": "attacker-nonce"}
    elif tamper == "signature":
        synthetic_idp["tampered"] = True
    elif tamper == "issuer":
        synthetic_idp["claims"] = {"iss": "https://evil.example.org"}
    else:
        synthetic_idp["claims"] = {"aud": "wrong-client"}
    reply = _callback(owner, state)
    assert reply.status_code == 303
    assert owner.get("/api/v1/mcp/account-links").json()["connections"] == []
    assert _callback(owner, state).status_code == 303
    assert synthetic_idp["exchanges"] == 1


def test_expired_challenge_is_rejected_before_provider_exchange(platform, synthetic_idp):
    owner, _ = _owner(platform, "expired")
    state = _start(owner, synthetic_idp)
    with session_scope() as db:
        challenge = db.scalar(select(McpOwnerOIDCChallenge).where(
            McpOwnerOIDCChallenge.state_hash == hashlib.sha256(state.encode()).hexdigest()))
        challenge.expires_at = now() - timedelta(minutes=1)
    assert _callback(owner, state).status_code == 303
    assert synthetic_idp["exchanges"] == 0
    assert owner.get("/api/v1/mcp/account-links").json()["connections"] == []


def test_same_external_identity_cannot_be_claimed_by_other_user(platform, synthetic_idp):
    alice, alice_uid = _owner(platform, "external-a")
    bob, _ = _owner(platform, "external-b")
    state_a = _start(alice, synthetic_idp)
    assert _callback(alice, state_a).status_code == 303
    state_b = _start(bob, synthetic_idp)
    assert _callback(bob, state_b).status_code == 303
    assert bob.get("/api/v1/mcp/account-links").json()["connections"] == []
    with session_scope() as db:
        link = db.scalar(select(McpIdentityLink).where(
            McpIdentityLink.issuer == ISSUER,
            McpIdentityLink.subject == synthetic_idp["subject"]))
        assert link.user_id == alice_uid


def test_revoked_owner_can_reverify_without_minting_cross_owner_access(platform, synthetic_idp):
    owner, _ = _owner(platform, "reconnect")
    state = _start(owner, synthetic_idp)
    assert _callback(owner, state).status_code == 303
    link_id = owner.get("/api/v1/mcp/account-links").json()["connections"][0]["id"]
    csrf = {"X-CSRF-Token": csrf_of(owner)}
    revocation = owner.post(
        f"/api/v1/mcp/account-links/{link_id}/disconnect",
        json={"confirm": "DISCONNECT CHATGPT"}, headers=csrf)
    assert revocation.status_code == 200
    assert owner.get("/api/v1/mcp/account-links").json()["connections"][0]["status"] == "disconnected"
    reattempt = _start(owner, synthetic_idp)
    assert _callback(owner, reattempt).status_code == 303
    entries = owner.get("/api/v1/mcp/account-links").json()["connections"]
    assert len(entries) == 1 and entries[0]["status"] == "connected"
    assert entries[0]["id"] == link_id
