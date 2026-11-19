"""MF-062: public, no-credential MCP readiness probe for one approved MF host.

Does not log tokens, fetch account records, follow redirects or grant access.
A passing probe verifies only the public resource boundary, never OAuth login.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

PRODUCTION_ORIGIN = "https://measuredfield-staging-web.onrender.com"
MAX_BODY = 32_768


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class PublicResult:
    status: int | None
    headers: dict[str, str]
    body: bytes = b""
    issue: str | None = None


def fetch_public(path: str) -> PublicResult:
    """Fetch only fixed public paths at the approved HTTPS host."""
    if path not in ("/healthz", "/readyz", "/mcp",
                    "/.well-known/oauth-protected-resource/mcp"):
        raise ValueError("Unsupported probe path")
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()),
                          _NoRedirect())
    req = Request(PRODUCTION_ORIGIN + path, headers={
        "Accept": "application/json, text/event-stream",
        "User-Agent": "MeasuredField-MCP-Readiness/1.0",
    })
    try:
        with opener.open(req, timeout=12) as response:
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                return PublicResult(response.status, {}, issue="oversized_response")
            return PublicResult(response.status, {
                k.lower(): v for k, v in response.headers.items()}, body)
    except HTTPError as exc:
        # Deliberately discard error bodies: they might contain diagnostics.
        return PublicResult(exc.code, {k.lower(): v for k, v in exc.headers.items()})
    except (URLError, OSError, ValueError):
        return PublicResult(None, {}, issue="transport_error")


def analyze(responses: dict[str, PublicResult]) -> dict:
    """All output is a fixed set of codes/statuses; never reflect remote text."""
    checks: dict[str, object] = {}
    health = responses.get("/healthz", PublicResult(None, {}))
    ready = responses.get("/readyz", PublicResult(None, {}))
    mcp = responses.get("/mcp", PublicResult(None, {}))
    meta = responses.get("/.well-known/oauth-protected-resource/mcp",
                         PublicResult(None, {}))

    checks["health_http"] = health.status
    checks["ready_http"] = ready.status
    checks["mcp_http"] = mcp.status
    checks["metadata_http"] = meta.status
    checks["app_healthy"] = health.status == 200 and ready.status == 200

    bearer = mcp.headers.get("www-authenticate", "").lower()
    checks["mcp_denies_anonymous"] = mcp.status == 401
    checks["mcp_bearer_challenge"] = (mcp.status == 401 and
                                     "bearer" in bearer and
                                     "resource_metadata=" in bearer)

    metadata_valid = False
    if meta.status == 200 and len(meta.body) <= MAX_BODY:
        try:
            data = json.loads(meta.body)
            if isinstance(data, dict):
                resource = data.get("resource")
                authorization_servers = data.get("authorization_servers")
                scopes = data.get("scopes_supported")
                metadata_valid = (
                    resource == PRODUCTION_ORIGIN + "/mcp"
                    and isinstance(authorization_servers, list)
                    and len(authorization_servers) >= 1
                    and all(_valid_issuer_url(x) for x in authorization_servers)
                    and isinstance(scopes, list)
                    and "mf:read" in scopes
                )
        except (ValueError, TypeError, UnicodeError):
            pass
    checks["oauth_resource_metadata_valid"] = metadata_valid
    checks["server_ready_for_chatgpt_authorization"] = (
        checks["app_healthy"] and checks["mcp_bearer_challenge"]
        and metadata_valid
    )
    # No probe without a consenting human login may claim connected status.
    checks["user_authentication_verified"] = False
    checks["chatgpt_plugin_connected"] = False
    checks["revocation_verified"] = False
    checks["connection_status"] = (
        "server_public_boundary_ready" if
        checks["server_ready_for_chatgpt_authorization"] else
        "server_boundary_incomplete"
    )
    return checks


def _valid_issuer_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 350:
        return False
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if not host or host == "localhost" or host.endswith(
                (".local", ".test", ".invalid", ".example")):
            return False
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return False
        return (url.scheme == "https" and
                not (url.username or url.password or url.query or url.fragment) and
                url.port in (None, 443))
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe public MeasuredField MCP only")
    parser.add_argument("--live", action="store_true",
                        help="Run four fixed public HTTPS requests; never log in")
    parser.add_argument("--json", action="store_true", help="Machine-readable statuses")
    args = parser.parse_args()
    if not args.live:
        output = {
            "connection_status": "not_probed",
            "user_authentication_verified": False,
            "chatgpt_plugin_connected": False,
            "revocation_verified": False,
        }
    else:
        paths = ("/healthz", "/readyz", "/mcp",
                 "/.well-known/oauth-protected-resource/mcp")
        output = analyze({path: fetch_public(path) for path in paths})
    print(json.dumps(output, indent=2 if args.json else None, sort_keys=True))
    return 0 if output.get("server_ready_for_chatgpt_authorization") else 2


if __name__ == "__main__":
    raise SystemExit(main())
