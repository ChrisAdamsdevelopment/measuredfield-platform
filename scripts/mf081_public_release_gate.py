"""MF-081 read-only production release gate. Never deploys, charges or reads DB data.

Checks a single pinned Render web service and Postgres metadata. There is no
manual "force pass" flag: backup/restore evidence needs independent approval.
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

API = "https://api.render.com/v1"
SERVICE = "srv-dbrk59jrjlhs73bha8pg"
DB = "dpg-dbrl0c3l550s73couo6g-a"
TARGET_BRANCH = "main"
PREDEPLOY = "python -m app.migrate"
PATHS = frozenset({
    f"/services/{SERVICE}",
    f"/services/{SERVICE}/deploys?limit=1",
    f"/services/{SERVICE}/env-vars?limit=100",
    f"/postgres/{DB}",
})


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_metadata(path: str, key: str) -> Any:
    if path not in PATHS or not key:
        raise ValueError("Unavailable approved metadata resource")
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
    req = Request(API + path, headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "User-Agent": "MeasuredField-readonly-release-gate/1",
    })
    with opener.open(req, timeout=15) as response:
        data = response.read(300_001)
        if len(data) > 300_000:
            raise ValueError("Metadata too large")
        return json.loads(data)


def evaluate(
    *, service: dict, deploy: dict, database: dict,
    env_names: set[str], backup_restore_verified: bool = False,
    local_main_sha: str | None = None,
) -> dict:
    blockers = []
    if service.get("id") != SERVICE or service.get("name") != "measuredfield-production-web":
        blockers.append("WRONG_WEB_SERVICE")
    if service.get("branch") != TARGET_BRANCH:
        blockers.append("NOT_ON_MAIN")
    if service.get("autoDeploy") != "no":
        blockers.append("AUTODEPLOY_MUST_BE_OFF")
    details = service.get("serviceDetails") or {}
    env_specific = details.get("envSpecificDetails") or {}
    if env_specific.get("preDeployCommand") != PREDEPLOY:
        blockers.append("MIGRATION_COMMAND_UNVERIFIED")
    if details.get("runtime") != "docker":
        blockers.append("UNEXPECTED_RUNTIME")
    if database.get("id") != DB or database.get("status") != "available":
        blockers.append("DATABASE_NOT_VERIFIED")
    plan = database.get("plan")
    if plan == "free":
        blockers.append("FREE_DATABASE_HAS_NO_MANAGED_RECOVERY")
    if not backup_restore_verified:
        blockers.append("NO_VERIFIED_PRODUCTION_RESTORE")
    if not {"DATABASE_URL", "MIGRATION_DATABASE_URL", "MF_SECRET_KEY"} <= env_names:
        blockers.append("CORE_DEPLOYMENT_CONFIG_MISSING")
    provider_names = ("MF_PAYMENT_PROVIDER", "MF_EMAIL_PROVIDER", "MF_AI_PROVIDER")
    # Never read or display environment VALUES in this audit. Separate provider
    # flags were verified as disabled by an authorized operator earlier.
    if not set(provider_names) <= env_names:
        blockers.append("PROVIDER_FLAGS_UNVERIFIED")
    current = deploy.get("commit") or {}
    current_sha = current.get("id") if isinstance(current, dict) else None
    status = deploy.get("status")
    if status not in ("live", "pending", "build_in_progress", "update_in_progress"):
        blockers.append("LIVE_DEPLOY_STATUS_UNEXPECTED")

    return {
        "gate": "blocked" if blockers else "metadata_only_requires_operator_approval",
        "source_branch": service.get("branch") if service.get("branch") in
            ("main", "feature/measuredfield-asset-production-20261112") else "unknown",
        "live_release_matches_local_main": (
            bool(local_main_sha) and current_sha == local_main_sha
        ),
        "migration_command_verified": "MIGRATION_COMMAND_UNVERIFIED" not in blockers,
        "db_compute_plan": plan if plan in ("free", "basic-256mb", "basic-1gb",
                                           "standard", "pro") else "other_or_unknown",
        "db_expiration": database.get("expiresAt") if plan == "free" else None,
        "backup_restore_verified": bool(backup_restore_verified),
        "blockers": sorted(set(blockers)),
        "may_deploy_without_explicit_approval": False,
        "chatgpt_oauth_verified": False,
        "mcp_connected": False,
        "credentials_or_customer_records_returned": False,
    }


def _local_main_sha() -> str | None:
    """Read local Git main only; never fetch, push, checkout or execute a hook."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/heads/main"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, timeout=6, check=False,
        )
        sha = result.stdout.strip()
        if result.returncode == 0 and len(sha) == 40 and all(
            c in "0123456789abcdef" for c in sha
        ):
            return sha
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def live_report(key: str) -> dict:
    service = read_metadata(f"/services/{SERVICE}", key)
    deployments = read_metadata(f"/services/{SERVICE}/deploys?limit=1", key)
    variables = read_metadata(f"/services/{SERVICE}/env-vars?limit=100", key)
    database = read_metadata(f"/postgres/{DB}", key)
    names = {item["envVar"]["key"] for item in variables
             if isinstance(item, dict)
             and isinstance(item.get("envVar"), dict)
             and isinstance(item["envVar"].get("key"), str)}
    latest = (deployments[0].get("deploy") or {}) if (
        isinstance(deployments, list) and deployments
        and isinstance(deployments[0], dict)
    ) else {}
    return evaluate(service=service, deploy=latest, database=database,
                    env_names=names, backup_restore_verified=False,
                    local_main_sha=_local_main_sha())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        result = {"gate": "not_probed", "may_deploy_without_explicit_approval": False}
    else:
        try:
            result = live_report(os.environ.get("RENDER_API_KEY", ""))
        except (HTTPError, URLError, OSError, ValueError, TypeError, KeyError,
                AttributeError, IndexError):
            result = {"gate": "render_read_failed",
                      "may_deploy_without_explicit_approval": False}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
