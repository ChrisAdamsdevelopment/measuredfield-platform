"""OAuth 2.1 resource-server verification for MeasuredField's MCP endpoint.

Identity is NEVER derived from MCP tool arguments or unsigned client claims.
An unconfigured subject-mapping or revocation lookup must deny all access.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient

from app.mcp_connector.gateway import VerifiedOAuthIdentity


class OAuthAccessDenied(PermissionError):
    pass


def _configured_https(value: str, *, mcp: bool = False) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 512:
        raise ValueError("OAuth requires an explicit HTTPS URL")
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or
                url.password or url.query or url.fragment or
                url.port not in (None, 443)):
            raise ValueError("OAuth requires canonical HTTPS endpoints")
        if url.hostname in {"localhost", "127.0.0.1", "::1"} or url.hostname.endswith(".local"):
            raise ValueError("Local OAuth settings are not deployable")
        if mcp and not url.path.endswith("/mcp"):
            raise ValueError("Resource URL must end in /mcp")
    except ValueError:
        raise
    return value


@dataclass(frozen=True)
class OAuthConfig:
    issuer: str
    jwks_url: str
    resource_url: str

    def __post_init__(self):
        _configured_https(self.issuer)
        if not urlsplit(self.issuer).path:
            # MCP SDK canonicalizes a bare issuer URL to end in '/'. Avoid
            # issuer metadata/JWT signature validation disagreement.
            raise ValueError("A root OAuth issuer must end with /")
        _configured_https(self.jwks_url)
        _configured_https(self.resource_url, mcp=True)

    @classmethod
    def from_env(cls) -> "OAuthConfig":
        return cls(
            issuer=os.environ.get("MF_MCP_ISSUER", ""),
            jwks_url=os.environ.get("MF_MCP_JWKS_URL", ""),
            resource_url=os.environ.get("MF_MCP_RESOURCE_URL", ""),
        )


@dataclass(frozen=True)
class VerifiedMCPToken:
    identity: VerifiedOAuthIdentity
    expires_at: int
    client_id: str
    jti: str


RevocationCheck = Callable[[str, str, str], bool]


class OAuthJWTVerifier:
    def __init__(self, config: OAuthConfig, *,
                 jwks_client: PyJWKClient | None = None,
                 is_revoked: RevocationCheck | None = None):
        self.config = config
        self.keys = jwks_client or PyJWKClient(
            config.jwks_url, timeout=5, cache_jwk_set=True, lifespan=300)
        # No token state/revocation integration -> DENY, never anonymous access.
        self.is_revoked = is_revoked

    def verify(self, raw: str) -> VerifiedMCPToken:
        if not isinstance(raw, str) or not 20 <= len(raw) <= 8192:
            raise OAuthAccessDenied("Invalid bearer token")
        try:
            header = jwt.get_unverified_header(raw)
            if (header.get("alg") not in ("RS256", "ES256") or
                    not isinstance(header.get("kid"), str) or
                    not 1 <= len(header["kid"]) <= 128 or
                    "jku" in header or "x5u" in header):
                raise OAuthAccessDenied("Untrusted JWT header")
            key = self.keys.get_signing_key_from_jwt(raw)
            claims = jwt.decode(raw, key.key, algorithms=["RS256", "ES256"],
                                issuer=self.config.issuer,
                                audience=self.config.resource_url,
                                options={"require": ["iss", "sub", "aud", "exp", "iat", "jti"]},
                                leeway=30)
        except (jwt.PyJWTError, KeyError, ValueError, TypeError, OSError):
            raise OAuthAccessDenied("Invalid bearer token") from None
        if claims.get("aud") != self.config.resource_url:
            raise OAuthAccessDenied("Wrong resource audience")
        sub, jti = claims.get("sub"), claims.get("jti")
        if (not isinstance(sub, str) or not sub.strip() or len(sub) > 255 or
                not isinstance(jti, str) or not 1 <= len(jti) <= 128):
            raise OAuthAccessDenied("Invalid subject or token identifier")
        scopes = claims.get("scope", claims.get("scp", []))
        if isinstance(scopes, str):
            scopes = scopes.split()
        if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):
            raise OAuthAccessDenied("Malformed scope claim")
        approved_scopes = frozenset(scopes)
        if "mf:read" not in approved_scopes:
            raise OAuthAccessDenied("Required scope mf:read is missing")
        client_id = claims.get("azp") or claims.get("client_id")
        if not isinstance(client_id, str) or not client_id or len(client_id) > 512:
            raise OAuthAccessDenied("OAuth client is not identified")
        if self.is_revoked is None:
            raise OAuthAccessDenied("Revocation lookup unavailable")
        try:
            if self.is_revoked(self.config.issuer, sub, jti):
                raise OAuthAccessDenied("Grant has been revoked")
        except OAuthAccessDenied:
            raise
        except Exception:
            raise OAuthAccessDenied("Revocation lookup unavailable") from None
        return VerifiedMCPToken(
            identity=VerifiedOAuthIdentity(
                issuer=self.config.issuer, subject=sub,
                audience=self.config.resource_url, scopes=approved_scopes),
            expires_at=int(claims["exp"]), client_id=client_id, jti=jti)
