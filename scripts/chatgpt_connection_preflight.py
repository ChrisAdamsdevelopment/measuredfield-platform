"""Offline ChatGPT MCP connector launch readiness checks, with no secrets or network.

This reports missing environment prerequisites only. It never concludes that
ChatGPT has connected: verified OAuth sign-in, plugin installation, HTTPS
staging delivery and device pairing are separate manual integration gates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mcp_plugin.config import McpPluginConfigurationError, _url  # noqa: E402
from scripts.staging_preflight import check_environment  # noqa: E402

MCP_NAMES = {
    "MF_MCP_OAUTH_ISSUER": "OAuth issuer",
    "MF_MCP_OAUTH_JWKS_URL": "JWKS",
    "MF_MCP_RESOURCE_URL": "MCP resource",
}
OIDC_NAMES = {
    "MF_OIDC_LINK_CLIENT_ID": "OIDC owner client",
    "MF_OIDC_LINK_CLIENT_SECRET": "OIDC owner secret",
    "MF_OIDC_LINK_AUTH_URL": "OIDC authorize endpoint",
    "MF_OIDC_LINK_TOKEN_URL": "OIDC token endpoint",
}
STEPS = (
    ("staging_https_reachable", "Operator must confirm real staging HTTPS and certificate."),
    ("oauth_provider_metadata_verified", "Operator must confirm issuer OAuth metadata, client registration, PKCE and audience-bound tokens."),
    ("owner_oidc_browser_login_verified", "Owner must complete the actual identity-provider browser consent/sign-in."),
    ("chatgpt_plugin_registered_and_installed", "Owner must create/install the ChatGPT plugin in a supported workspace."),
    ("chatgpt_real_read_tool_verified", "Owner must invoke read-only MCP tool in ChatGPT and check business/tenant evidence."),
    ("windows_device_owner_paired", "Separately pair a Windows folder companion and verify stop/revocation before remote files."),
)


def analyze_environment(env: Mapping[str, str]) -> dict:
    """Pure offline checks. Never return environment contents or user identifiers."""
    config = dict(env)
    problems: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def fail(key: str, code: str) -> None:
        value = {"key": key, "code": code}
        if value not in problems:
            problems.append(value)

    def warn(key: str, code: str) -> None:
        value = {"key": key, "code": code}
        if value not in warnings:
            warnings.append(value)

    for problem in check_environment(config):
        (fail if problem.level == "FAIL" else warn)(
            problem.key, "STAGING_" + ("INVALID" if problem.level == "FAIL" else "REVIEW")
        )
    for key in MCP_NAMES:
        if not config.get(key):
            fail(key, "MCP_MISSING")
    urls = {}
    if all(config.get(key) for key in MCP_NAMES):
        for key, label in MCP_NAMES.items():
            try:
                _url(label, config[key], True)
                urls[key] = urlsplit(config[key])
            except McpPluginConfigurationError:
                fail(key, "MCP_HTTPS_URL_INVALID")
        if len(urls) == len(MCP_NAMES):
            if urls["MF_MCP_OAUTH_ISSUER"].hostname != urls["MF_MCP_OAUTH_JWKS_URL"].hostname:
                fail("MF_MCP_OAUTH_JWKS_URL", "MCP_ISSUER_MISMATCH")
            if urls["MF_MCP_RESOURCE_URL"].path != "/mcp":
                fail("MF_MCP_RESOURCE_URL", "MCP_RESOURCE_PATH_INVALID")
    for key in OIDC_NAMES:
        if not config.get(key):
            fail(key, "OWNER_OIDC_MISSING")
    if config.get("MF_OIDC_LINK_CLIENT_ID") and len(config["MF_OIDC_LINK_CLIENT_ID"]) > 250:
        fail("MF_OIDC_LINK_CLIENT_ID", "OWNER_OIDC_CLIENT_INVALID")
    if config.get("MF_OIDC_LINK_CLIENT_SECRET") and len(config["MF_OIDC_LINK_CLIENT_SECRET"]) > 4096:
        fail("MF_OIDC_LINK_CLIENT_SECRET", "OWNER_OIDC_SECRET_INVALID")
    issuer = (urls["MF_MCP_OAUTH_ISSUER"].hostname
              if "MF_MCP_OAUTH_ISSUER" in urls else None)
    for key in ("MF_OIDC_LINK_AUTH_URL", "MF_OIDC_LINK_TOKEN_URL"):
        if not config.get(key):
            continue
        try:
            _url(OIDC_NAMES[key], config[key], True)
            parts = urlsplit(config[key])
        except McpPluginConfigurationError:
            fail(key, "OWNER_OIDC_ENDPOINT_INVALID")
            continue
        if not parts.path.strip("/"):
            fail(key, "OWNER_OIDC_ENDPOINT_MISSING_PATH")
        if issuer and parts.hostname != issuer:
            fail(key, "OWNER_OIDC_ISSUER_MISMATCH")
    try:
        current_sdk = version("mcp")
    except PackageNotFoundError:
        current_sdk = None
    if current_sdk != "2.3.0":
        warn("mcp", "LOCAL_SDK_VERSION_DIFFERS_FROM_PRODUCTION_PIN")
    checks = {
        "environment_configured": not problems,
        "staging_https_reachable": False,
        "oauth_provider_metadata_verified": False,
        "owner_oidc_browser_login_verified": False,
        "chatgpt_plugin_registered_and_installed": False,
        "chatgpt_real_read_tool_verified": False,
        "windows_device_owner_paired": False,
    }
    return {
        "status": "missing_configuration" if problems else "configuration_only_online_verification_required",
        "configured": not problems,
        "ready_for_chatgpt": False,
        "checks": checks,
        "blockers": sorted(problems, key=lambda r: (r["key"], r["code"])),
        "warnings": sorted(warnings, key=lambda r: (r["key"], r["code"])),
        "manual_acceptance_gates": [
            {"id": name, "status": "not_verified", "required_action": message}
            for name, message in STEPS
        ],
        "data_accessed": "environment_configuration_only",
        "external_calls_made": 0,
        "credentials_disclosed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline no-secret ChatGPT connector prerequisite audit")
    parser.add_argument("--json", action="store_true", help="Safe JSON summary; never prints values")
    args = parser.parse_args(argv)
    result = analyze_environment(os.environ)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("CHATGPT_CONNECTOR_PREFLIGHT: " + result["status"])
        print("Blocking configuration entries:", len(result["blockers"]))
        for item in result["blockers"]:
            print("  CONFIGURE", item["key"], item["code"])
        print("Warnings:", len(result["warnings"]))
        for item in result["warnings"]:
            print("  REVIEW", item["key"], item["code"])
        print("ChatGPT plugin installed and real browser OAuth read verified: NO")
        print("No provider calls, credential output, deployment, user login or paid execution.")
    return 2 if result["blockers"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
