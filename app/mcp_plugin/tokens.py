"""Strict OAuth JWT verification; token claims never directly grant tenant access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import jwt
from jwt import PyJWKClient

from app.mcp_connector.gateway import VerifiedOAuthIdentity
from .config import McpPluginSettings
from .links import linked_subject_user_id


class OAuthAccessDenied(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedBearer:
    identity: VerifiedOAuthIdentity
    expires_at: int
    issued_at: int
    client_id: str


class OAuthJWTVerifier:
    def __init__(self, settings: McpPluginSettings, *,
                 jwks_client: Any = None,
                 resolve_subject: Callable[[str, str, int], Any] = linked_subject_user_id):
        self.settings = settings
        self.jwks = (jwks_client if jwks_client is not None else
                     PyJWKClient(settings.jwks_url, cache_jwk_set=True, lifespan=300, timeout=5))
        self.resolve_subject = resolve_subject

    def verify(self, token: str) -> VerifiedBearer:
        if not isinstance(token, str) or not 30 <= len(token) <= 8192:
            raise OAuthAccessDenied("Invalid OAuth access token.")
        try:
            header = jwt.get_unverified_header(token)
            if (header.get("alg") not in ("RS256", "ES256") or
                    any(v in header for v in ("jku", "jwk", "x5u", "x5c")) or
                    not isinstance(header.get("kid"), str) or
                    not 1 <= len(header["kid"]) <= 200):
                raise OAuthAccessDenied("Untrusted OAuth signing key metadata.")
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256", "ES256"],
                issuer=self.settings.issuer, audience=self.settings.resource_url,
                leeway=30, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except OAuthAccessDenied:
            raise
        except (jwt.PyJWTError, OSError, ValueError, TypeError, KeyError):
            raise OAuthAccessDenied("Invalid OAuth access token.") from None
        subject, expiry, issued = claims.get("sub"), claims.get("exp"), claims.get("iat")
        if (not isinstance(subject, str) or not subject.strip() or len(subject) > 250
                or type(expiry) is not int or type(issued) is not int or expiry <= issued):
            raise OAuthAccessDenied("Malformed OAuth subject or token timestamps.")
        if claims.get("resource", self.settings.resource_url) != self.settings.resource_url:
            raise OAuthAccessDenied("OAuth token has the wrong resource.")
        raw_scopes = claims.get("scope", claims.get("scp", []))
        if isinstance(raw_scopes, str):
            scopes = frozenset(raw_scopes.split())
        elif isinstance(raw_scopes, list) and all(isinstance(x, str) for x in raw_scopes):
            scopes = frozenset(raw_scopes)
        else:
            raise OAuthAccessDenied("Malformed OAuth permissions.")
        if self.settings.read_scope not in scopes:
            raise OAuthAccessDenied("OAuth read scope is missing.")
        client_id = claims.get("client_id", claims.get("azp"))
        if not isinstance(client_id, str) or not client_id or len(client_id) > 250:
            raise OAuthAccessDenied("OAuth client is missing.")
        if self.resolve_subject(self.settings.issuer, subject, issued) is None:
            raise OAuthAccessDenied("OAuth subject has not been linked by the owner.")
        return VerifiedBearer(
            identity=VerifiedOAuthIdentity(
                issuer=self.settings.issuer, subject=subject,
                audience=self.settings.resource_url, scopes=scopes),
            expires_at=expiry, issued_at=issued, client_id=client_id)
