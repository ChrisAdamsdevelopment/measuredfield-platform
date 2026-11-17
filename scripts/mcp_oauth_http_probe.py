"""MF-044: offline, real-SDK MCP OAuth HTTP 401/discovery contract gate.

Run from an isolated environment with MCP 2.3.0:
    python -B scripts/mcp_oauth_http_probe.py

Uses Starlette in-process TestClient and a rejecting synthetic token verifier.
No external network calls, real identities, credentials, or app data.
"""
from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from starlette.testclient import TestClient

if version("mcp") != "2.3.0":
    raise RuntimeError(f"Expected isolated official MCP 2.3.0; got {version('mcp')}")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Path bootstrap above intentionally precedes local imports (script may run from any CWD).
from app.mcp_plugin.config import McpPluginSettings  # noqa: E402
from app.mcp_plugin.server import create_server  # noqa: E402
from app.mcp_plugin.tokens import OAuthAccessDenied  # noqa: E402


RESOURCE = "https://connector.synthetic.test/mcp"
ORIGIN = "https://connector.synthetic.test"
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"
METADATA_URL = ORIGIN + METADATA_PATH
ISSUER = "https://login.synthetic.test"


class RejectEveryBearer:
    """No JWKS fetch or other outbound network is possible in this probe."""

    def verify(self, token: str):
        raise OAuthAccessDenied("Synthetic bearer rejected")


def check_challenge(header: str, case: str):
    if not header or not re.match(r"^Bearer(?:\s|$)", header, re.I):
        raise AssertionError(f"{case}: missing OAuth Bearer WWW-Authenticate challenge")
    matches = re.findall(r'\bresource_metadata="([^"]+)"', header, flags=re.I)
    if len(matches) != 1:
        raise AssertionError(f"{case}: expected one quoted resource_metadata value")
    locator = matches[0]
    url = urlsplit(locator)
    if (
        locator != METADATA_URL or url.scheme != "https"
        or url.hostname != "connector.synthetic.test"
        or url.username is not None or url.password is not None
        or url.query or url.fragment or url.port is not None
    ):
        raise AssertionError(f"{case}: OAuth discovery points at an unexpected resource")


def main():
    settings = McpPluginSettings(
        issuer=ISSUER,
        jwks_url=ISSUER + "/.well-known/jwks.json",
        resource_url=RESOURCE,
    )
    app = create_server(settings, token_verifier=RejectEveryBearer()).streamable_http_app(
        json_response=True
    )
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "synthetic-contract-check", "version": "1"},
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    for malformed in (
        "",
        "Basic realm=\"test\"",
        "Bearer",
        'Bearer resource_metadata="http://connector.synthetic.test/.well-known/oauth-protected-resource/mcp"',
        'Bearer resource_metadata="https://relay.evil.test/.well-known/oauth-protected-resource/mcp"',
        'Bearer resource_metadata="https://connector.synthetic.test/.well-known/oauth-protected-resource/mcp?x=1"',
    ):
        try:
            check_challenge(malformed, "negative_fixture")
        except AssertionError:
            pass
        else:
            raise AssertionError("Malformed challenge passed validation")
    print("MF044_MALFORMED_CHALLENGE_REJECTION_PASS", flush=True)

    with TestClient(app, base_url=ORIGIN) as client:
        discovery = client.get(METADATA_PATH)
        assert discovery.status_code == 200, discovery.status_code
        document = discovery.json()
        assert document["resource"] == RESOURCE
        assert {str(url).rstrip("/") for url in document["authorization_servers"]} == {ISSUER}
        assert "mf:read" in document["scopes_supported"]
        print("MF044_DISCOVERY_PASS", flush=True)

        for case, auth in (("no_authorization", None), ("invalid_bearer", "Bearer invalid")):
            req_headers = {**headers}
            if auth:
                req_headers["Authorization"] = auth
            response = client.post("/mcp", json=payload, headers=req_headers)
            assert response.status_code == 401, (case, response.status_code)
            check_challenge(response.headers.get("www-authenticate", ""), case)
            print(f"MF044_401_CHALLENGE_PASS {case}", flush=True)

    print("MF044_OAUTH_HTTP_CONFORMANCE_PASS", flush=True)


if __name__ == "__main__":
    main()
