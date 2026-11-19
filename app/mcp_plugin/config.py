"""Strict production settings; no fake URL, secret or unauthenticated fallback."""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


class McpPluginConfigurationError(ValueError):
    pass


def _url(name: str, value: str, allow_path: bool) -> None:
    if not isinstance(value, str) or not 10 <= len(value) <= 350:
        raise McpPluginConfigurationError(f"{name} must be an HTTPS URL.")
    try:
        p = urlsplit(value)
        port = p.port
    except ValueError:
        raise McpPluginConfigurationError(f"{name} is not a valid URL.") from None
    host = p.hostname or ""
    if (p.scheme != "https" or not host or p.username or p.password or
            p.query or p.fragment or (port is not None and port != 443) or
            host != host.strip(".") or not host.isascii()):
        raise McpPluginConfigurationError(f"{name} must be a secure HTTPS URL.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise McpPluginConfigurationError(f"{name} cannot be an IP literal.")
    if host in ("localhost", "example.com") or host.endswith(
            (".localhost", ".local", ".test", ".example", ".invalid")):
        raise McpPluginConfigurationError(f"{name} needs a configured real domain.")
    if not allow_path and p.path not in ("", "/"):
        raise McpPluginConfigurationError(f"{name} cannot contain a path.")
    if p.path.startswith("//") or "/../" in p.path or "/./" in p.path:
        raise McpPluginConfigurationError(f"{name} contains an unsafe path.")


@dataclass(frozen=True, slots=True)
class McpPluginSettings:
    issuer: str
    jwks_url: str
    resource_url: str
    read_scope: str = "mf:read"
    # Separate, explicit owner-granted scope. Never implied by mf:read alone.
    write_scope: str = "mf:drafts.write"

    @classmethod
    def from_env(cls) -> "McpPluginSettings":
        names = ("MF_MCP_OAUTH_ISSUER", "MF_MCP_OAUTH_JWKS_URL", "MF_MCP_RESOURCE_URL")
        missing = [n for n in names if not os.getenv(n)]
        if missing:
            raise McpPluginConfigurationError("MCP OAuth not configured: " + ", ".join(missing))
        issuer, jwks, resource = (os.environ[n] for n in names)
        _url("OAuth issuer", issuer, True)
        _url("JWKS", jwks, True)
        _url("MCP resource", resource, True)
        if urlsplit(issuer).hostname != urlsplit(jwks).hostname:
            raise McpPluginConfigurationError("JWKS must share the trusted issuer hostname.")
        if urlsplit(resource).path != "/mcp":
            raise McpPluginConfigurationError("Resource URL must be the exact /mcp path.")
        return cls(issuer=issuer, jwks_url=jwks, resource_url=resource)
