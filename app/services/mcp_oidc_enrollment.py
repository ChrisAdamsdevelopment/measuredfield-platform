"""Owner-authenticated OIDC authorization-code/PKCE linking for read-only MCP.

Never trusts a browser-supplied user ID, email or OAuth subject. The owner is
the MeasuredField session, the external identity is a signed OIDC ID token, and
the pair must converge through a short-lived one-time transaction.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import TenantSession
from ..models import McpIdentityLink, McpOwnerOIDCChallenge, now
from ..mcp_plugin.config import McpPluginConfigurationError, McpPluginSettings, _url

STATE_TTL_MINUTES = 10


class OwnerEnrollmentDenied(PermissionError):
    """Generic rejection: never expose OAuth codes, provider replies or JWTs."""


class OwnerEnrollmentUnavailable(ValueError):
    """Explicitly unconfigured owner registration, not an auth fallback."""


@dataclass(frozen=True, slots=True)
class OIDCOwnerSettings:
    issuer: str
    jwks_url: str
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> "OIDCOwnerSettings":
        try:
            plugin = McpPluginSettings.from_env()
            auth = os.environ["MF_OIDC_LINK_AUTH_URL"]
            token = os.environ["MF_OIDC_LINK_TOKEN_URL"]
            cid = os.environ["MF_OIDC_LINK_CLIENT_ID"]
            secret = os.environ["MF_OIDC_LINK_CLIENT_SECRET"]
            base = get_settings().public_base_url.rstrip("/")
            redirect = base + "/app/connections/callback"
            for name, url in (
                ("OIDC authorize endpoint", auth),
                ("OIDC token endpoint", token),
                ("OIDC redirect", redirect),
            ):
                _url(name, url, True)
            if (not cid.strip() or len(cid) > 250 or not secret.strip() or
                    len(secret) > 4096 or
                    urlsplit(auth).hostname != urlsplit(plugin.issuer).hostname or
                    urlsplit(token).hostname != urlsplit(plugin.issuer).hostname or
                    not urlsplit(auth).path.strip("/") or not urlsplit(token).path.strip("/") or
                    urlsplit(redirect).path != "/app/connections/callback" or
                    urlsplit(redirect).hostname not in get_settings().app_host_set):
                raise ValueError("OIDC owner client endpoints or redirect are not configured.")
            if (not get_settings().secret_key or len(get_settings().secret_key) < 32
                    or get_settings().secret_key.startswith("dev-only")):
                raise ValueError("The MeasuredField encryption key is not configured.")
        except (KeyError, ValueError, McpPluginConfigurationError):
            raise OwnerEnrollmentUnavailable("An approved OIDC owner identity provider is required.") from None
        return cls(plugin.issuer, plugin.jwks_url, auth, token, cid, secret, redirect)


def config_available() -> bool:
    try:
        OIDCOwnerSettings.from_env()
        return True
    except OwnerEnrollmentUnavailable:
        return False


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _cipher() -> Fernet:
    secret = get_settings().secret_key
    if not isinstance(secret, str) or len(secret) < 32 or secret.startswith("dev-only"):
        # Tests use a genuine (synthetic) 32+ character fixture key.
        raise OwnerEnrollmentUnavailable("An approved MeasuredField encryption key is required.")
    key = hashlib.sha256(b"measuredfield.oidc.pkce.v1:" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


@dataclass(frozen=True, slots=True)
class OIDCBegin:
    authorize_url: str
    state: str
    nonce: str


def start_owner_link(db: TenantSession, *, user_id, session_id, settings: OIDCOwnerSettings) -> OIDCBegin:
    """Requires caller-authenticated session + CSRF; no identity is taken from input."""
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    expiry = now() + timedelta(minutes=STATE_TTL_MINUTES)
    db.add(McpOwnerOIDCChallenge(
        state_hash=_digest(state), issuer=settings.issuer,
        user_id=user_id, session_id=session_id,
        nonce_hash=_digest(nonce),
        pkce_ciphertext=_cipher().encrypt(verifier.encode("ascii")),
        expires_at=expiry,
    ))
    authorization_url = settings.authorization_endpoint + "?" + urlencode({
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": "openid profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return OIDCBegin(authorization_url, state, nonce)


def consume_owner_link(db: TenantSession, *, user_id, session_id, state: str,
                       settings: OIDCOwnerSettings) -> tuple[bytes, str]:
    """Use a row lock and commit consumption BEFORE provider network exchange."""
    if (not isinstance(state, str) or not 32 <= len(state) <= 150 or
            not re.fullmatch(r"[A-Za-z0-9_\-]+", state)):
        raise OwnerEnrollmentDenied("This authorization attempt is invalid or expired.")
    row = db.scalar(select(McpOwnerOIDCChallenge).where(
        McpOwnerOIDCChallenge.state_hash == _digest(state)).with_for_update())
    if (row is None or row.user_id != user_id or row.session_id != session_id
            or row.issuer != settings.issuer or row.consumed_at is not None
            or row.expires_at <= now()):
        raise OwnerEnrollmentDenied("This authorization attempt is invalid or expired.")
    try:
        verifier = _cipher().decrypt(row.pkce_ciphertext, ttl=STATE_TTL_MINUTES * 60 + 60)
        verifier.decode("ascii")
    except (InvalidToken, ValueError, UnicodeError):
        raise OwnerEnrollmentDenied("This authorization attempt is invalid or expired.") from None
    nonce_hash = row.nonce_hash
    row.consumed_at = now()
    row.pkce_ciphertext = b"consumed"
    # Prevent two parallel callbacks from exchanging the same authorization
    # code. No OAuth network call happens while the challenge row is locked.
    db.commit()
    return verifier, nonce_hash


def exchange_and_verify(code: str, verifier: bytes, nonce_hash: str,
                        settings: OIDCOwnerSettings, *,
                        token_transport=None, jwks_client=None) -> str:
    """Exchange authorization code only with pinned endpoint, verify signed ID token."""
    if not isinstance(code, str) or not 8 <= len(code) <= 2048:
        raise OwnerEnrollmentDenied("Provider authorization was not completed.")
    try:
        if token_transport is None:
            with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
                response = client.post(settings.token_endpoint, data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.redirect_uri,
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "code_verifier": verifier.decode("ascii"),
                })
        else:
            response = token_transport(settings, code, verifier)
        if (response.status_code != 200 or len(response.content) > 16384 or
                not response.headers.get("content-type", "").lower().startswith("application/json")):
            raise OwnerEnrollmentDenied("Provider authorization was not completed.")
        payload = response.json()
        token = payload.get("id_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not 30 <= len(token) <= 8192:
            raise OwnerEnrollmentDenied("The provider identity was not verified.")
        header = jwt.get_unverified_header(token)
        if (header.get("alg") not in ("RS256", "ES256") or
                not isinstance(header.get("kid"), str) or not 1 <= len(header["kid"]) <= 200
                or any(key in header for key in ("jku", "jwk", "x5u", "x5c"))):
            raise OwnerEnrollmentDenied("The provider identity was not verified.")
        keys = jwks_client if jwks_client is not None else jwt.PyJWKClient(
            settings.jwks_url, timeout=5, cache_jwk_set=True, lifespan=300)
        signing = keys.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, signing, algorithms=["RS256", "ES256"],
                            issuer=settings.issuer, audience=settings.client_id, leeway=30,
                            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]})
        subject = claims.get("sub")
        nonce = claims.get("nonce")
        if (not isinstance(subject, str) or not 1 <= len(subject) <= 250
                or not subject.strip() or not isinstance(nonce, str)
                or not secrets.compare_digest(_digest(nonce), nonce_hash)
                or type(claims.get("iat")) is not int or type(claims.get("exp")) is not int
                or claims["exp"] <= claims["iat"]
                or (isinstance(claims.get("aud"), list) and len(claims["aud"]) > 1
                    and claims.get("azp") != settings.client_id)
                or (claims.get("azp") is not None and claims["azp"] != settings.client_id)):
            raise OwnerEnrollmentDenied("The provider identity was not verified.")
        return subject
    except OwnerEnrollmentDenied:
        raise
    except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, KeyError,
            UnicodeError, OSError):
        raise OwnerEnrollmentDenied("The provider identity was not verified.") from None


def finish_owner_link(db: TenantSession, *, user_id, subject: str,
                      settings: OIDCOwnerSettings) -> McpIdentityLink:
    """A verified immutable external subject can be owned by only one local user."""
    if not isinstance(subject, str) or not subject or len(subject) > 250:
        raise OwnerEnrollmentDenied("The provider identity was not verified.")
    row = db.scalar(select(McpIdentityLink).where(
        McpIdentityLink.issuer == settings.issuer,
        McpIdentityLink.subject == subject).with_for_update())
    if row is not None:
        if row.user_id != user_id:
            raise OwnerEnrollmentDenied("This provider account is linked to another owner.")
        if row.revoked_at is None:
            return row
        row.revoked_at = None
        row.linked_at = now()  # Reject any token minted before re-consent.
        db.flush()
        return row
    row = McpIdentityLink(
        issuer=settings.issuer, subject=subject, user_id=user_id,
        verified_method="oidc_pkce_owner")
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise OwnerEnrollmentDenied("This provider account cannot be linked.") from None
    return row
