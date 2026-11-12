"""Bridge to the PBC-derived growth evaluator (vendor/growth_engine.mjs).

Reused from shared_core/growth_bridge.py. The evaluator is a pure function run in a
short-lived Node process with a stripped environment, so no application secret
(database URL, payment or email keys) is visible to it. If Node is unavailable the
caller gets ``None`` and the page says the review could not run; nothing is invented.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "vendor"
_SAFE_ENV = ("PATH", "SystemRoot", "WINDIR", "PATHEXT", "TMP", "TEMP", "HOME")


UNAVAILABLE_ACTIONS = {"search-console-access", "visibility-baseline"}


class GrowthBridgeError(RuntimeError):
    pass


def node_available() -> bool:
    return shutil.which("node") is not None


def assess_business(payload: dict) -> dict:
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(raw.encode("utf-8")) > 120_000:
        raise GrowthBridgeError("Payload exceeds bridge limit")
    env = {k: os.environ[k] for k in _SAFE_ENV if k in os.environ}
    try:
        run = subprocess.run(["node", str(VENDOR / "growth_cli.mjs")], input=raw, text=True,
                             encoding="utf-8", capture_output=True, timeout=8, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GrowthBridgeError("Growth evaluator unavailable") from exc
    if run.returncode != 0 or len(run.stdout) > 1_000_000:
        raise GrowthBridgeError("Growth evaluator rejected the request")
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise GrowthBridgeError("Invalid growth evaluator response") from exc


def review_for_org(business_name: str, measured: dict, *, days: int = 30) -> dict | None:
    """Run the evaluator on first-party measured counters only.

    ``measured`` comes from site_events (page views, form starts, inquiries recorded by
    this platform). Search Console and AI-visibility inputs are absent because no
    connector is active, and the evaluator reports them as unknown.
    """
    has_site_data = any(measured.values())
    site = None
    if has_site_data:
        site = {"totals": {"pageViews": measured.get("page_view", 0),
                           "leadRequests": measured.get("lead", 0),
                           "formStarts": measured.get("form_start", 0),
                           "formSubmits": measured.get("lead", 0)},
                "pages": [{"path": "/", "views": measured.get("page_view", 0)}],
                "sources": []}
    try:
        report = assess_business({"businessName": business_name, "days": days, "site": site, "ai": None,
                                  "searchConsole": {"status": "unavailable",
                                                    "reason": "Search Console is not connected"}})
    except GrowthBridgeError:
        # Render's native Python runtime may not include Node.js. Keep the
        # owner experience functional without claiming evaluator parity.
        from .native_growth import review_from_counts
        return review_from_counts(business_name, measured, days=days)
    report["evidenceStatus"]["site"] = "measured_first_party" if has_site_data else "unknown"
    # The PBC evaluator also proposes connecting Search Console and running an AI-visibility
    # baseline. Neither connector exists here yet, so those are shown under "Connections"
    # as unavailable rather than as experiments the owner cannot actually run.
    report["analysis"]["actions"] = [a for a in report["analysis"].get("actions", [])
                                     if a.get("id") not in UNAVAILABLE_ACTIONS]
    return report
