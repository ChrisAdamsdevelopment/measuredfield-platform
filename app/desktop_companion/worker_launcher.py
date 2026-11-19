"""Owner-only launcher for the outbound HTTPS Work-mode computer companion.

This module must only be imported by the interactive desktop owner application.
It is never exposed as a ChatGPT MCP tool, and cannot bypass an expired pairing.
The subprocess receives only an opaque *pairing id*, never the local pairing
token, device credential, OAuth token or user credentials on its command line.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from app.account_link.runtime_guard import AccountRuntimeGuard
from app.local_connect.store import AccessDenied
from app.paired_connect.registry import PairingRegistry


def launch_owner_work_session(
    pairing_id: str,
    *,
    registry: PairingRegistry | None = None,
    runner: Callable = subprocess.Popen,
    guard_factory: Callable = AccountRuntimeGuard,
) -> subprocess.Popen:
    """Start an owner-confirmed Work session. Call only after owner GUI consent."""
    registry = registry or PairingRegistry()
    token = registry.token_for_local_launcher(pairing_id)
    scope = registry.authorize(pairing_id, token)
    if not scope.get("can_write"):
        raise AccessDenied("Select an active, owner-approved Work session")
    # A linked, LIVE 'work' device is mandatory before launching the process.
    guard_factory(registry.home).check(action="write")
    if getattr(sys, "frozen", False):
        raise AccessDenied("This desktop build does not include the Work-mode worker")
    command = [
        sys.executable, "-B", "-m", "app.desktop_companion.work_poller",
        "--pairing-id", pairing_id,
    ]
    kwargs = {
        "cwd": str(Path(__file__).resolve().parents[2]),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = runner(command, **kwargs)
    except OSError:
        raise AccessDenied(
            "Unable to start this Work session. Please reconnect from the desktop app."
        ) from None
    # This is only a process-start receipt. It does not claim remote connection
    # or successful action until the worker provides its real receipt.
    return process
