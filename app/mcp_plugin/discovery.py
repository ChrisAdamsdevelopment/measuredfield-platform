"""Expose MCP protocol discovery without waiving OAuth on protected tools.

The installed MCP SDK 2.3.0 globally wraps the Streamable HTTP route in
RequireAuthMiddleware whenever a TokenVerifier is configured. ChatGPT needs
initialize/tools/list before it can present the per-tool OAuth consent UI.

We retain the SDK's verified bearer AuthenticationMiddleware and original
RequireAuthMiddleware for every data-bearing request. The only bypass is for
four explicitly allowlisted, read-only MCP bootstrap methods *without* an
Authorization header. Tool handlers already revalidate JWT signature, scope,
identity link and tenant membership on every consequential call.
"""
from __future__ import annotations

import json

from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from starlette.routing import Route

MAX_DISCOVERY_BODY = 64 * 1024
PUBLIC_METHODS = frozenset({
    "initialize", "notifications/initialized", "tools/list", "ping",
})


class DiscoveryAwareAuth:
    """ASGI dispatch to unprotected transport for safe discovery only."""

    def __init__(self, public_app, protected_app):
        if not isinstance(protected_app, RequireAuthMiddleware):
            raise TypeError("Must preserve SDK's RequireAuthMiddleware")
        self.public_app = public_app
        self.protected_app = protected_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.protected_app(scope, receive, send)

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        # Invalid, suspicious, or partial bearer tokens must not be downgraded
        # into an anonymous discovery session.
        if b"authorization" in headers:
            return await self.protected_app(scope, receive, send)
        # Some valid streaming clients omit Content-Length and use chunked HTTP.
        # The bounded incremental read below enforces the same 64 KiB cap.
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                return await self.protected_app(scope, receive, send)
            if content_length < 2 or content_length > MAX_DISCOVERY_BODY:
                return await self.protected_app(scope, receive, send)

        chunks = []
        count = 0
        while True:
            event = await receive()
            if event["type"] != "http.request":
                return await self.protected_app(scope, receive, send)
            fragment = event.get("body", b"")
            count += len(fragment)
            if count > MAX_DISCOVERY_BODY:
                return await self.protected_app(scope, receive, send)
            chunks.append(fragment)
            if not event.get("more_body", False):
                break

        body = b"".join(chunks)
        try:
            payload = json.loads(body)
            is_discovery = (
                isinstance(payload, dict)
                and payload.get("jsonrpc") == "2.0"
                and payload.get("method") in PUBLIC_METHODS
                and payload.get("params", {}) in ({}, None)
                and (payload.get("method") == "notifications/initialized"
                     or type(payload.get("id")) in (int, str))
            )
            # initialize legitimately supplies non-empty protocol capabilities.
            if isinstance(payload, dict) and payload.get("method") == "initialize":
                is_discovery = (
                    payload.get("jsonrpc") == "2.0"
                    and type(payload.get("id")) in (str, int)
                    and isinstance(payload.get("params"), dict)
                    and all(k in ("protocolVersion", "capabilities", "clientInfo", "extensions")
                            for k in payload["params"])
                )
        except (ValueError, TypeError):
            is_discovery = False

        if not is_discovery:
            # We have already consumed the request body, but RequireAuthMiddleware
            # rejects unauthenticated requests without reading it.
            return await self.protected_app(scope, receive, send)

        forwarded = False

        async def replay():
            nonlocal forwarded
            if not forwarded:
                forwarded = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.public_app(scope, replay, send)


def enable_anonymous_mcp_discovery(app):
    """Replace only the SDK /mcp transport route; preserve its auth middleware."""
    routes = app.router.routes
    indices = [
        i for i, route in enumerate(routes)
        if isinstance(route, Route) and route.path == "/mcp"
    ]
    if len(indices) != 1:
        raise RuntimeError("Expected one SDK Streamable HTTP MCP route")
    index = indices[0]
    route = routes[index]
    protected = route.endpoint
    if not isinstance(protected, RequireAuthMiddleware):
        raise RuntimeError("MCP SDK auth configuration changed; do not downgrade")
    app.router.routes[index] = Route(
        route.path,
        endpoint=DiscoveryAwareAuth(protected.app, protected),
        methods=route.methods,
        name=route.name,
    )
    return app
