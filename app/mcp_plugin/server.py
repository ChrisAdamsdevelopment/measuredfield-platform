"""Official MCP v2, OAuth-protected and read-only MeasuredField gateway.

Run only after verified staging OAuth, a linked identity and approved HTTPS.
Launch with uvicorn --factory app.mcp_plugin.server:create_app.
No standalone default or static identity, no public unlinked mode.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Annotated
from urllib.parse import urlsplit
from pydantic import Field

from app.mcp_connector.gateway import (
    ConnectorAccessDenied, ReadOnlyBusinessGateway, VerifiedOAuthIdentity,
)
from .config import McpPluginSettings
from .links import linked_subject_user_id
from .tokens import OAuthAccessDenied, OAuthJWTVerifier


def oauth_tool_metadata(scope: str, *, profile: bool = False) -> dict:
    """Advertise explicit tool-level OAuth security per current ChatGPT MCP contract."""
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("A nonblank OAuth tool scope is required")
    metadata = {"securitySchemes": [{"type": "oauth2", "scopes": [scope]}]}
    if profile:
        metadata["openai/profile"] = True
    return metadata


def create_server(settings: McpPluginSettings, *, gateway=None, token_verifier=None):
    """Create tools and SDK OAuth middleware, not a live process or outbound call."""
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.auth.provider import AccessToken
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations
    from pydantic import AnyHttpUrl

    jwt_verifier = token_verifier or OAuthJWTVerifier(settings)
    actual_gateway = gateway or ReadOnlyBusinessGateway(
        issuer=settings.issuer, resource_audience=settings.resource_url,
        resolve_subject=linked_subject_user_id)

    class LinkedTokenVerifier:
        async def verify_token(self, token: str):
            try:
                verified = await asyncio.to_thread(jwt_verifier.verify, token)
            except OAuthAccessDenied:
                return None
            return AccessToken(
                token=token, client_id=verified.client_id,
                scopes=sorted(verified.identity.scopes),
                expires_at=verified.expires_at,
                subject=verified.identity.subject,
                resource=settings.resource_url,
            )

    mcp = MCPServer(
        "MeasuredField",
        token_verifier=LinkedTokenVerifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.issuer),
            resource_server_url=AnyHttpUrl(settings.resource_url),
            required_scopes=[settings.read_scope],
            validate_token_resource=True,
        ),
    )

    def checked_bearer():
        verified = get_access_token()
        # A direct/in-memory/stdio tool invocation must never bypass HTTP auth.
        if (verified is None or not isinstance(verified.subject, str) or
                not verified.subject or not isinstance(verified.token, str) or
                settings.read_scope not in verified.scopes or
                verified.resource != settings.resource_url):
            raise ConnectorAccessDenied("Verified HTTP OAuth identity is required")
        # Defense in depth: the MCP SDK's stdio/in-memory contexts can bypass
        # the HTTP authentication middleware. Never trust their AccessToken
        # object alone; validate its signature and current account link AGAIN
        # at the consequential read-only tool boundary.
        try:
            bearer = jwt_verifier.verify(verified.token)
        except OAuthAccessDenied:
            raise ConnectorAccessDenied("OAuth identity is not authorized") from None
        if (bearer.identity.subject != verified.subject or
                bearer.client_id != verified.client_id):
            raise ConnectorAccessDenied("OAuth session identity changed")
        return bearer

    def identity() -> VerifiedOAuthIdentity:
        return checked_bearer().identity

    @mcp.tool(
        title="Connected MeasuredField account",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope, profile=True),
    )
    def get_connected_profile() -> dict:
        """Return the stable opaque ID of the currently authorized MeasuredField owner."""
        bearer = checked_bearer()
        user_id = linked_subject_user_id(
            bearer.identity.issuer, bearer.identity.subject, bearer.issued_at)
        if not isinstance(user_id, uuid.UUID):
            raise ConnectorAccessDenied("The owner account link is unavailable")
        return {"id": str(user_id), "name": "MeasuredField account"}

    @mcp.tool(
        title="List my MeasuredField businesses",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope),
    )
    def list_my_businesses() -> dict:
        """List only businesses belonging to your connected MeasuredField account."""
        businesses = actual_gateway.businesses_for(identity())
        return {"businesses": businesses, "count": len(businesses)}

    @mcp.tool(
        title="Measured business overview",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope),
    )
    def get_business_overview(business_id: str) -> dict:
        """Show first-party inquiry and website counts for an authorized business."""
        return actual_gateway.overview_for(identity(), business_id)

    @mcp.tool(
        title="Recent customer inquiries",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope),
    )
    def list_inquiries(
        business_id: str, limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> dict:
        """Read recent inquiries for one business you belong to."""
        rows = actual_gateway.inquiries_for(identity(), business_id, limit)
        return {"business_id": business_id, "inquiries": rows, "count": len(rows)}

    @mcp.tool(
        title="One customer inquiry",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope),
    )
    def get_inquiry(business_id: str, inquiry_id: str) -> dict:
        """Get a single inquiry after current membership and tenant checks."""
        return actual_gateway.inquiry_for(identity(), business_id, inquiry_id)

    @mcp.tool(
        title="Prepare reply preview, never send",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope),
    )
    def preview_reply(
        business_id: str, inquiry_id: str,
        message: Annotated[str, Field(min_length=1, max_length=4000)],
    ) -> dict:
        """Preview text only. Never save, email, post, publish or contact customers."""
        record = actual_gateway.inquiry_for(identity(), business_id, inquiry_id)
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 4000:
            raise ValueError("A nonblank draft is required")
        return {
            "status": "preview_only_not_saved_or_sent",
            "business_id": business_id,
            "inquiry_id": str(record["id"]),
            "proposed_message": message.strip(),
            "owner_action_needed": "Review and approve separately in MeasuredField",
        }

    from .census_market_tools import available_markets, market_snapshot

    @mcp.tool(
        title="Available Census service-area markets",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope),
    )
    def list_supported_census_markets(business_id: str) -> dict:
        """Return approved market choices only for a business you can access."""
        return available_markets(
            identity=identity(), business_id=business_id, gateway=actual_gateway,
        )

    @mcp.tool(
        title="Official Census market snapshot",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        meta=oauth_tool_metadata(settings.read_scope),
    )
    def get_census_market_snapshot(
        business_id: str, market_id: Annotated[str, Field(min_length=1, max_length=40)],
    ) -> dict:
        """Read source-labelled Census aggregates; never named competitors."""
        return market_snapshot(
            identity=identity(), business_id=business_id, area_slug=market_id,
            gateway=actual_gateway,
        )

    return mcp


def create_app():
    """Factory fails at startup if HTTPS/OAuth configuration is absent.

    OAuth discovery endpoints and HTTP auth middleware are owned by the SDK.
    Production deployment, DNS and ChatGPT registration are deliberately separate.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    settings = McpPluginSettings.from_env()
    # The SDK defaults to a localhost-only Host allowlist, even when deployed
    # behind a public HTTPS proxy. Derive the *exact* public origin from our
    # already-validated resource URL; never disable DNS-rebinding protection.
    resource_host = urlsplit(settings.resource_url).hostname
    if not resource_host:  # defensive check even if URL validation changes
        raise ValueError("MCP resource must have a validated public hostname")
    origin = f"https://{resource_host}"
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[resource_host, f"{resource_host}:443"],
        allowed_origins=[origin, f"{origin}:443"],
    )
    server = create_server(settings)
    return server.streamable_http_app(
        json_response=True,
        host=resource_host,
        transport_security=transport_security,
    )
