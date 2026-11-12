"""MeasuredField read-only MCP Streamable HTTP composition.

Deploy separately with uvicorn --factory app.mcp_auth.server:build_app.
This endpoint is NOT installed in the main FastAPI app. The stock factory
intentionally denies all user tokens until subject-link and revocation stores
are integrated and an OAuth authorization provider is configured.
"""
from __future__ import annotations

from typing import Callable
import uuid

from app.mcp_connector.gateway import (
    ReadOnlyBusinessGateway, VerifiedOAuthIdentity, unconfigured_subject_resolver,
)
from .identity import OAuthAccessDenied, OAuthConfig, OAuthJWTVerifier


def create_server(
    config: OAuthConfig,
    *,
    subject_resolver: Callable[[str, str], uuid.UUID | None] = unconfigured_subject_resolver,
    is_revoked: Callable[[str, str, str], bool] | None = None,
):
    """Use MCP SDK v2 to expose only authenticated, RLS-bound read tools."""
    # These imports are optional for the main web service, required for MCP worker.
    from pydantic import AnyHttpUrl
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.auth.provider import AccessToken
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    verifier = OAuthJWTVerifier(config, is_revoked=is_revoked)
    gateway = ReadOnlyBusinessGateway(
        issuer=config.issuer, resource_audience=config.resource_url,
        resolve_subject=subject_resolver,
    )

    class TokenVerifier:
        async def verify_token(self, raw_token: str) -> AccessToken | None:
            try:
                proof = verifier.verify(raw_token)
            except OAuthAccessDenied:
                return None
            return AccessToken(
                token=raw_token,
                client_id=proof.client_id,
                scopes=sorted(proof.identity.scopes),
                expires_at=proof.expires_at,
                subject=proof.identity.subject,
                resource=config.resource_url,
            )

    service = MCPServer(
        "MeasuredField Business Assistant",
        token_verifier=TokenVerifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.issuer),
            resource_server_url=AnyHttpUrl(config.resource_url),
            required_scopes=["mf:read"],
            validate_token_resource=True,
        ),
    )
    hint = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    def caller() -> VerifiedOAuthIdentity:
        token = get_access_token()
        if (token is None or not isinstance(token.subject, str) or
                not token.subject or "mf:read" not in token.scopes):
            raise OAuthAccessDenied("Verified OAuth user access is required")
        return VerifiedOAuthIdentity(
            issuer=config.issuer, subject=token.subject,
            audience=config.resource_url, scopes=frozenset(token.scopes),
        )

    @service.tool(title="My connected MeasuredField businesses", annotations=hint)
    def list_my_businesses() -> dict:
        """List only the connected user's authorized active businesses."""
        return {"businesses": gateway.businesses_for(caller())}

    @service.tool(title="Business performance overview", annotations=hint)
    def get_business_overview(business_id: str) -> dict:
        """Get one authorized business's first-party inquiries and website count."""
        return gateway.overview_for(caller(), business_id)

    @service.tool(title="Recent inquiries from my business", annotations=hint)
    def list_recent_inquiries(business_id: str, limit: int = 20) -> dict:
        """List recent inquiries after verifying organization membership."""
        return {"inquiries": gateway.inquiries_for(caller(), business_id, limit)}

    @service.tool(title="One inquiry from my business", annotations=hint)
    def get_inquiry(business_id: str, inquiry_id: str) -> dict:
        """Read one own-business inquiry, never send or edit it."""
        return gateway.inquiry_for(caller(), business_id, inquiry_id)

    return service


def build_app():
    """Require configured HTTPS OAuth issuer; reject every token by default."""
    cfg = OAuthConfig.from_env()
    service = create_server(cfg)  # no revocation store => no authorized calls
    return service.streamable_http_app(json_response=True)
