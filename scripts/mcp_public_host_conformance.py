"""MF-045: real MCP 2.3.0 factory host-security conformance, all in-process.

Run with the isolated MCP SDK environment. The accepted synthetic verifier
does not read JWKS or prove identity; it exists solely to get past the
authentication layer and exercise the *actual* SDK Host and Origin checks.
"""
from __future__ import annotations

import os
import sys
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

if version("mcp") != "2.3.0":
    raise RuntimeError(f"Expected MCP 2.3.0, found {version('mcp')}")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.mcp_plugin.config import McpPluginConfigurationError  # noqa: E402
import app.mcp_plugin.server as server_mod  # noqa: E402

RESOURCE = "https://connector.example.net/mcp"
ORIGIN = "https://connector.example.net"
ISSUER = "https://auth.example.net"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Authorization": "Bearer synthetic",
}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "mf045-offline", "version": "1"},
    },
}


class AcceptForTransportOnly:
    def verify(self, token):
        if token != "synthetic":
            raise ValueError("Only fixed synthetic test token accepted")
        return SimpleNamespace(
            client_id="offline-client",
            expires_at=None,
            identity=SimpleNamespace(subject="synthetic-owner", scopes={"mf:read"}),
        )


def assert_status(client, expected, *, host=None, origin=None, forward_host=None, authenticated=True):
    headers = dict(HEADERS)
    if not authenticated:
        headers.pop("Authorization")
    if host:
        headers["Host"] = host
    if origin is not None:
        headers["Origin"] = origin
    if forward_host:
        headers["X-Forwarded-Host"] = forward_host
        headers["Forwarded"] = f"host={forward_host}"
    response = client.post("/mcp", json=INIT, headers=headers)
    assert response.status_code == expected, (
        expected, response.status_code, response.text[:200], host, origin
    )
    return response


def main():
    credentials = {
        "MF_MCP_OAUTH_ISSUER": ISSUER,
        "MF_MCP_OAUTH_JWKS_URL": ISSUER + "/.well-known/jwks.json",
        "MF_MCP_RESOURCE_URL": RESOURCE,
    }
    real_create = server_mod.create_server

    # Runs the actual production factory and its real configuration validation;
    # only the cryptographic verifier is synthetic (host checks are real SDK).
    with patch.dict(os.environ, credentials):
        with patch.object(
            server_mod, "create_server",
            side_effect=lambda settings: real_create(
                settings, token_verifier=AcceptForTransportOnly()
            ),
        ):
            app = server_mod.create_app()

            with TestClient(app, base_url=ORIGIN) as client:
                assert_status(client, 200)
                assert_status(client, 200, host="connector.example.net")
                assert_status(client, 200, host="connector.example.net:443")
                assert_status(client, 200, origin=ORIGIN)
                assert_status(client, 200, origin=ORIGIN + ":443")
                print("MF045_VALID_PUBLIC_HOST_200_PASS", flush=True)

                for foreign in (
                    "localhost",
                    "127.0.0.1",
                    "connector.example.net.attacker.invalid",
                    "attacker.invalid",
                    "connector.example.net:444",
                ):
                    assert_status(
                        client, 421, host=foreign,
                        forward_host="connector.example.net",
                    )
                print("MF045_FOREIGN_HOST_421_PASS", flush=True)

                for foreign in (
                    "https://attacker.invalid",
                    "http://connector.example.net",
                    "https://connector.example.net.attacker.invalid",
                ):
                    assert_status(client, 403, origin=foreign)
                print("MF045_ORIGIN_403_PASS", flush=True)

                unauthorized = assert_status(client, 401, authenticated=False)
                challenge = unauthorized.headers.get("www-authenticate", "")
                assert "Bearer" in challenge
                assert (
                    ORIGIN + "/.well-known/oauth-protected-resource/mcp"
                ) in challenge
                discovery = client.get("/.well-known/oauth-protected-resource/mcp")
                assert discovery.status_code == 200
                metadata = discovery.json()
                assert metadata["resource"] == RESOURCE
                assert {x.rstrip("/") for x in metadata["authorization_servers"]} == {ISSUER}
                assert "mf:read" in metadata["scopes_supported"]
                print("MF045_UNAUTHORIZED_DISCOVERY_UNCHANGED_PASS", flush=True)

            with patch.dict(os.environ, {"MF_MCP_RESOURCE_URL": "http://connector.example.net/mcp"}):
                try:
                    server_mod.create_app()
                except McpPluginConfigurationError:
                    print("MF045_INSECURE_RESOURCE_FAIL_CLOSED_PASS", flush=True)
                else:
                    raise AssertionError("Production factory accepted HTTP resource")

    print("MF045_PUBLIC_HOST_CONFORMANCE_PASS", flush=True)


if __name__ == "__main__":
    main()
