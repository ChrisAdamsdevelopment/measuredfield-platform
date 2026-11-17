"""MF-078: read-only Render release preflight for the existing MeasuredField service.

No deployments, provider changes, token disclosure, user login, or plugin edits.
The default mode is offline. --live only reads fixed Render metadata endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
RENDER_API = "https://api.render.com/v1"
SERVICE_ID = "srv-dbrk59jrjlhs73bha8pg"
SERVICE_NAME = "measuredfield-production-web"
SERVICE_URL = "https://measuredfield-staging-web.onrender.com"
REPO_URL = "https://github.com/ChrisAdamsdevelopment/measuredfield-platform"
EXPECTED_BRANCH = "feature/measuredfield-asset-production-20261112"

REQUIRED_NAMES = frozenset({
    "MF_CENSUS_API_KEY", "MF_ENABLE_REMOTE_MCP",
    "MF_MCP_OAUTH_ISSUER", "MF_MCP_OAUTH_JWKS_URL",
    "MF_MCP_RESOURCE_URL", "MF_OIDC_LINK_CLIENT_ID",
    "MF_OIDC_LINK_CLIENT_SECRET", "MF_OIDC_LINK_AUTH_URL",
    "MF_OIDC_LINK_TOKEN_URL",
})
MAX_RENDER_BODY = 300_000
TIMEOUT_SECONDS = 15


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_render_json(*, path: str, key: str) -> Any:
    """Fixed allowlist prevents arbitrary URL, redirects and secret logging."""
    paths = {
        f"/services/{SERVICE_ID}",
        f"/services/{SERVICE_ID}/env-vars?limit=100",
        f"/services/{SERVICE_ID}/deploys?limit=5",
    }
    if path not in paths:
        raise ValueError("Unapproved Render metadata path")
    if not key:
        raise ValueError("Render read-only authentication is unavailable")
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), _NoRedirect())
    req = Request(RENDER_API + path, headers={
        "Authorization": "Bearer " + key, "Accept": "application/json",
        "User-Agent": "MeasuredField-release-preflight/1.0",
    })
    with opener.open(req, timeout=TIMEOUT_SECONDS) as response:
        payload = response.read(MAX_RENDER_BODY + 1)
        if len(payload) > MAX_RENDER_BODY:
            raise ValueError("Oversized Render metadata response")
        return json.loads(payload)


def _git_counts(live_sha: str) -> dict:
    """Local Git ancestry only; no fetch, checkout, push or branch changes."""
    if (not isinstance(live_sha, str) or len(live_sha) != 40
            or any(ch not in "0123456789abcdef" for ch in live_sha)):
        return {"available": False}
    args = [
        ["rev-parse", "--verify", "HEAD"],
        ["cat-file", "-t", live_sha],
        ["rev-list", "--count", f"{live_sha}..HEAD"],
        ["rev-list", "--count", f"HEAD..{live_sha}"],
    ]
    results = []
    for item in args:
        try:
            proc = subprocess.run(
                ["git", *item], cwd=ROOT, text=True,
                capture_output=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {"available": False}
        if proc.returncode:
            return {"available": False}
        results.append(proc.stdout.strip())
    try:
        if results[1] != "commit":
            return {"available": False}
        return {
            "available": True,
            "local_head": results[0],
            "local_ahead": int(results[2]),
            "live_only": int(results[3]),
        }
    except ValueError:
        return {"available": False}


def assess_release(
    *, service: dict, deploy: dict, env_keys: set[str], git: dict,
) -> dict:
    """Produce only safe statuses and environment NAMES. Never return values."""
    blockers: list[str] = []
    details = service.get("serviceDetails")
    details = details if isinstance(details, dict) else {}
    commit = deploy.get("commit")
    commit = commit if isinstance(commit, dict) else {}
    if service.get("id") != SERVICE_ID or service.get("name") != SERVICE_NAME:
        blockers.append("WRONG_RENDER_SERVICE")
    if service.get("repo") != REPO_URL:
        blockers.append("REPO_MISMATCH")
    if details.get("url") != SERVICE_URL:
        blockers.append("PUBLIC_HOST_MISMATCH")
    if service.get("branch") != EXPECTED_BRANCH:
        blockers.append("PRODUCTION_BRANCH_CHANGED")
    if deploy.get("status") != "live":
        blockers.append("DEPLOY_NOT_LIVE")
    live_sha = commit.get("id")
    if not isinstance(live_sha, str) or len(live_sha) != 40:
        blockers.append("NO_VALID_LIVE_COMMIT")
    missing = sorted(REQUIRED_NAMES - set(env_keys))
    if missing:
        blockers.append("REQUIRED_MCP_CONFIGURATION_NOT_PRESENT")
    if not git.get("available"):
        blockers.append("LOCAL_GIT_ANCESTRY_UNVERIFIED")
    elif git.get("local_ahead", 0) > 0 or git.get("live_only", 0) > 0:
        blockers.append("LIVE_AND_TESTED_SOURCE_DIFFER")
    if git.get("available") and git.get("live_only", 0) > 0:
        blockers.append("PRODUCTION_BRANCH_DIVERGED")

    # Safe metadata alone can NEVER prove OAuth, owner consent or tool access.
    return {
        "release_preflight": "blocked" if blockers else "metadata_only_manual_gates",
        "service_is_expected": "WRONG_RENDER_SERVICE" not in blockers,
        "app_https_host_matches": "PUBLIC_HOST_MISMATCH" not in blockers,
        "live_deploy_status": deploy.get("status") if deploy.get("status") in
            ("live", "build_failed", "pre_deploy_failed", "update_failed") else "unknown",
        "source_state": {
            "git_verified": bool(git.get("available")),
            "local_ahead_commits": git.get("local_ahead") if git.get("available") else None,
            "production_only_commits": git.get("live_only") if git.get("available") else None,
        },
        "missing_environment_names": missing,
        "environment_group_inheritance_checked": False,
        "blockers": sorted(set(blockers)),
        "oauth_provider_audience_verified": False,
        "owner_consent_verified": False,
        "chatgpt_plugin_connected": False,
        "revocation_verified": False,
        "safe_to_enable_mcp": False,
        "can_deploy_without_owner_approval": False,
        "live_customer_records_accessed": False,
        "credentials_disclosed": False,
    }


def live_report(key: str) -> dict:
    """Read only approved Render metadata. No account or business APIs."""
    base = f"/services/{SERVICE_ID}"
    service = _read_render_json(path=base, key=key)
    vars_raw = _read_render_json(path=base + "/env-vars?limit=100", key=key)
    deploys = _read_render_json(path=base + "/deploys?limit=5", key=key)
    # Values from env-var objects are discarded immediately without logging.
    env_keys = {
        x["envVar"]["key"] for x in vars_raw if
        isinstance(x, dict) and isinstance(x.get("envVar"), dict)
        and isinstance(x["envVar"].get("key"), str)
    }
    deploy = (deploys[0].get("deploy", {}) if isinstance(deploys, list)
              and deploys and isinstance(deploys[0], dict) else {})
    git = _git_counts((deploy.get("commit") or {}).get("id", ""))
    return assess_release(service=service, deploy=deploy, env_keys=env_keys,
                          git=git)


def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(description="Safe MCP live-release preflight")
    args.add_argument("--live", action="store_true",
                      help="Fetch pinned, read-only Render metadata; no deployment")
    args.add_argument("--json", action="store_true")
    options = args.parse_args(argv)
    if not options.live:
        data = {"release_preflight": "not_probed",
                "chatgpt_plugin_connected": False,
                "safe_to_enable_mcp": False,
                "credentials_disclosed": False}
    else:
        try:
            data = live_report(os.environ.get("RENDER_API_KEY", ""))
        except (HTTPError, URLError, OSError, ValueError, TypeError, KeyError, AttributeError):
            # Never reflect response body/URL/error message or credential.
            data = {"release_preflight": "render_read_failed",
                    "chatgpt_plugin_connected": False,
                    "safe_to_enable_mcp": False,
                    "credentials_disclosed": False}
    print(json.dumps(data, indent=2 if options.json else None, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
