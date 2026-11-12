"""Run with isolated official MCP SDK v2 interpreter; no live OAuth/network/DB.

This file is intentionally outside standard pytest collection so the ordinary
web service can be installed without the optional MCP SDK.
"""
from __future__ import annotations

from starlette.testclient import TestClient
from app.mcp_auth.identity import OAuthConfig
from app.mcp_auth.server import create_server

cfg = OAuthConfig(
    issuer="https://login.test.measuredfield.example/",
    jwks_url="https://login.test.measuredfield.example/jwks.json",
    resource_url="https://mcp.test.measuredfield.example/mcp",
)
mcp = create_server(cfg)  # no revocation callback or subject link -> hard deny
app = mcp.streamable_http_app(json_response=True)

with TestClient(app) as client:
    meta = client.get("/.well-known/oauth-protected-resource/mcp")
    print("MCP_META_STATUS", meta.status_code, meta.text[:300])
    assert meta.status_code == 200
    data = meta.json()
    assert data["resource"] == cfg.resource_url
    assert cfg.issuer in data["authorization_servers"]

    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "MF-test-client", "version": "0.0.1"}},
    }
    rsp = client.post("/mcp", json=body,
                      headers={"Accept": "application/json, text/event-stream",
                               "Content-Type": "application/json"})
    print("MCP_UNAUTH_STATUS", rsp.status_code)
    print("MCP_AUTH_CHALLENGE", rsp.headers.get("www-authenticate", "")[:240])
    assert rsp.status_code == 401
    assert "Bearer" in rsp.headers.get("www-authenticate", "")
print("MCP_HTTP_AUTH_DENY_PASS")
