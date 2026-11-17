"""Process-independent account authority check for the private paired stdio MCP pilot.

Every paired file tool must recheck the real workspace device *on that request*;
the Windows UI is not an authorization oracle. A transient error denies the
request and locally revokes active pairings. No background service or tunnel.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from app.local_connect import AccessDenied
from .client import DeviceApi, DeviceClientError, resolve_trusted_origin
from .secret_store import SecureStoreUnavailable, WindowsCredentialStore


class AccountRuntimeGuard:
    """Fail closed whenever a private local pairing has been account-linked.

    The legacy offline pilot is permitted ONLY when the device has never been
    linked: no account state, no saved DPAPI credential. A tampered/invalid
    account state or a stored secret with 'not_connected' is not an escape hatch.
    """

    def __init__(self, home: str | Path, *, credential_store=None,
                 client_factory: Callable | None = None, registry=None):
        self.home = Path(home).expanduser().resolve()
        self.state_file = self.home / "account_connection.json"
        self.secrets = credential_store if credential_store is not None else WindowsCredentialStore(self.home)
        self.client_factory = client_factory or DeviceApi
        self.registry = registry

    def _credential_present(self) -> bool:
        if isinstance(self.secrets, WindowsCredentialStore):
            return self.secrets.file.exists() or self.secrets.file.is_symlink()
        try:
            return bool(self.secrets.load())
        except (SecureStoreUnavailable, OSError, ValueError):
            return True  # Broken secret store must never make access 'unlinked'.

    def _deny(self) -> None:
        """Revoke all active local pairings before reporting a generic denial."""
        # Late import avoids a cycle with paired_connect.registry.BoundSession.
        from app.paired_connect.registry import PairingRegistry

        try:
            registry = self.registry or PairingRegistry(self.home)
            for pairing in registry.list():
                if pairing["status"] == "active":
                    registry.revoke(pairing["pairing_id"])
        finally:
            raise AccessDenied(
                "This computer's MeasuredField connection is not verified. "
                "Local project access stopped. Reconnect it from the owner workspace."
            ) from None

    def _read_account(self) -> dict | None:
        if self.state_file.is_symlink() or (
            hasattr(self.state_file, "is_junction") and self.state_file.is_junction()
        ):
            self._deny()
        try:
            raw = self.state_file.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            self._deny()
        if len(raw) > 4096:
            self._deny()
        try:
            state = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            self._deny()
        if not isinstance(state, dict):
            self._deny()
        return state

    def require_never_linked_local_pilot(self) -> None:
        """Deny the older unpaired stdio server on *any* account-connected PC.

        Unlike paired tools, an unpaired tool can never use the cloud status to
        authorize itself. It is only for a clean never-linked local pilot.
        Merely having account metadata or a DPAPI credential is disqualifying,
        even if the UI says disconnected, read-only, or cannot contact the API.
        """
        state = self._read_account()
        if state is not None or self._credential_present():
            raise AccessDenied(
                "The legacy local server is disabled for account-linked computers. "
                "Use the owner-approved paired connection launcher instead."
            )

    def check(self, *, action: str = "read") -> None:
        """Synchronously authorize each tool, not just when GUI is open.

        For an active linked account, no cached authorization is accepted: a
        fresh pinned-origin HTTPS call must report the exact active device.
        Read-only permission is enforced even against old local write pairings.
        """
        if action not in ("list", "read", "propose"):
            self._deny()
        state = self._read_account()
        if state is None or state.get("status") == "not_connected":
            if self._credential_present():
                self._deny()
            if state is not None and state.get("status") == "not_connected" and (
                    state.get("device_id") or state.get("server_url") or
                    state.get("access") or state.get("business")):
                self._deny()
            # Legacy owner-local pilot: a clean never-linked computer can still
            # use approved, short-lived local pairings; no remote access exists.
            return
        if state.get("status") != "active":
            self._deny()
        saved_url = state.get("server_url")
        device_id = state.get("device_id")
        if not isinstance(saved_url, str) or not saved_url or (
                not isinstance(device_id, str) or not device_id):
            self._deny()
        try:
            origin = resolve_trusted_origin(saved_url)
            credential = self.secrets.load()
            if not credential:
                self._deny()
            api = self.client_factory(origin)
            try:
                status = api.status(credential)
            finally:
                api.close()
        except (DeviceClientError, SecureStoreUnavailable, OSError, ValueError):
            self._deny()
        # Even a successful remote call must not race a local UI disconnect.
        # Avoid trusting a changed or damaged state written during the request.
        state_after = self._read_account()
        if state_after != state:
            self._deny()
        if status.status != "active" or status.device_id != device_id or (
                status.access not in ("read", "suggest")):
            self._deny()
        if action == "propose" and status.access != "suggest":
            self._deny()
