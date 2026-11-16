"""Owner-side account linking, fail-closed local revocation and pairing permissions."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.local_connect import AccessDenied, LocalConnect
from app.local_connect_ui.pairing_controller import PairingOwnerController
from app.paired_connect.registry import PairingRegistry

from .client import DeviceApi, DeviceClientError, DeviceRevoked
from .secret_store import WindowsCredentialStore, SecureStoreUnavailable


@dataclass(frozen=True)
class AccountView:
    status: str = "not_connected"
    server_url: str = ""
    device_id: str = ""
    access: str = ""
    business: str = ""
    name: str = ""
    check_number: str = ""  # IN MEMORY ONLY while pending; never persisted.
    notice: str = ""


class AccountLinkController:
    """Owner-only UI controller. Never available through model-callable tools."""

    def __init__(self, local: LocalConnect | None = None, *,
                 credential_store: object | None = None,
                 client_factory: Callable[[str], DeviceApi] | None = None,
                 registry: PairingRegistry | None = None):
        self.local = local or LocalConnect()
        self.home: Path = self.local.home
        self.path = self.home / "account_connection.json"
        self.secrets = credential_store if credential_store is not None else WindowsCredentialStore(self.home)
        self.client_factory = client_factory or DeviceApi
        self.registry = registry or PairingRegistry(self.home)
        self._state = self._load()
        self._check_number = ""
        self._notice = ""

    def _load(self) -> dict:
        if self.path.is_symlink():
            raise AccessDenied("Account state cannot be a symbolic link.")
        try:
            source = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {"status": "not_connected", "server_url": "", "device_id": "",
                    "access": "", "business": "", "name": ""}
        if not isinstance(source, dict):
            return {"status": "not_connected", "server_url": "", "device_id": "",
                    "access": "", "business": "", "name": ""}
        allowed = ("status", "server_url", "device_id", "access", "business", "name")
        values = {k: source.get(k, "") for k in allowed}
        values = {k: v if isinstance(v, str) and len(v) <= 250 else "" for k, v in values.items()}
        if values["status"] not in ("not_connected", "pending", "active", "revoked", "unverified"):
            values["status"] = "not_connected"
        return values

    def _save(self) -> None:
        if self.path.is_symlink():
            raise AccessDenied("Account state cannot be a symbolic link.")
        # Whitelist fields so neither the pairing code nor device credential can be saved.
        fields = ("status", "server_url", "device_id", "access", "business", "name")
        payload = {key: self._state.get(key, "") for key in fields}
        temporary = self.home / "account_connection.tmp"
        if temporary.is_symlink():
            raise AccessDenied("Account state cannot use a symbolic link.")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def view(self) -> AccountView:
        return AccountView(**self._state, check_number=self._check_number,
                           notice=self._notice)

    def can_suggest_edits(self) -> bool:
        # Existing local pilot remains usable unlinked. Once account-linked,
        # active Suggest changes is required for local proposal permissions.
        return self._state["status"] == "not_connected" or (
            self._state["status"] == "active" and self._state["access"] == "suggest")

    def _stop_local_connections(self) -> None:
        # Revoke ALL local MCP pairing capabilities, including previously issued sessions.
        for row in self.registry.list():
            if row["status"] == "active":
                self.registry.revoke(row["pairing_id"])

    def _read_only_policy(self) -> None:
        if not self.can_suggest_edits():
            for row in self.registry.list():
                if row["status"] == "active" and row["can_suggest_edits"]:
                    self.registry.revoke(row["pairing_id"])

    def _revoke(self, notice: str) -> AccountView:
        # Local-first cleanup even if the server is unavailable or returned malformed JSON.
        self._stop_local_connections()
        self.secrets.clear()
        self._state.update(status="revoked", access="", business="")
        self._check_number = ""
        self._notice = notice
        self._save()
        return self.view()

    def connect(self, server_url: str, code: str, device_name: str) -> AccountView:
        if self._state["status"] in ("active", "pending", "unverified"):
            raise AccessDenied("Disconnect the existing computer before pairing again.")
        api = self.client_factory(server_url)
        try:
            intro = api.pair(code, device_name)
            try:
                # Store first, only then remember any non-secret metadata.
                self.secrets.save(intro.credential)
            except (SecureStoreUnavailable, OSError):
                try:
                    api.disconnect(intro.credential)
                except DeviceClientError:
                    pass
                raise SecureStoreUnavailable("Could not protect the credential on this computer.") from None
            self._state = {"status": "pending", "server_url": api.origin,
                           "device_id": intro.device_id, "access": "",
                           "business": "", "name": device_name[:80]}
            self._check_number = intro.check_number
            self._notice = "Enter this check number in your signed-in MeasuredField workspace."
            self._save()
            self._read_only_policy()
            return self.view()
        finally:
            api.close()

    def refresh(self) -> AccountView:
        if self._state["status"] not in ("pending", "active", "unverified"):
            return self.view()
        credential = self.secrets.load()
        if not credential:
            return self._revoke("This computer has no saved credential and was disconnected locally.")
        api = self.client_factory(self._state["server_url"])
        try:
            status = api.status(credential)
        except DeviceRevoked:
            return self._revoke("The workspace disconnected this computer. Local trusted connections stopped.")
        except DeviceClientError:
            self._state["status"] = "unverified"
            self._stop_local_connections()  # fail closed until verified again
            self._notice = "The workspace cannot be verified. Local trusted connections stopped."
            self._save()
            return self.view()
        finally:
            api.close()
        if status.device_id != self._state["device_id"]:
            return self._revoke("The workspace reported a different computer; connections stopped.")
        self._state.update(status=status.status, access=status.access,
                           business=status.business, name=status.name)
        if status.status == "active":
            self._check_number = ""
            self._notice = "Connected. Each project folder still needs its own permission."
        else:
            self._notice = "Awaiting confirmation in the signed-in workspace."
        self._save()
        self._read_only_policy()
        return self.view()

    def disconnect(self) -> AccountView:
        credential = None
        try:
            credential = self.secrets.load()
        except SecureStoreUnavailable:
            pass
        if credential and self._state.get("server_url"):
            try:
                api = self.client_factory(self._state["server_url"])
                try:
                    api.disconnect(credential)
                finally:
                    api.close()
            except DeviceClientError:
                pass  # local revocation still succeeds even if the server is unavailable
        return self._revoke("Computer disconnected locally. Existing local connections stopped.")


class AccountBoundPairingController(PairingOwnerController):
    """Enforce server-side Read only when the owner creates a new local pairing."""
    def __init__(self, account: AccountLinkController):
        super().__init__(account.local)
        self.account = account

    def pair(self, name: str, project_ids: list[str], *, minutes: int = 15,
             allow_suggestions: bool = False, confirmed: bool = False,
             confirmed_suggestions: bool = False) -> dict:
        if allow_suggestions and not self.account.can_suggest_edits():
            raise AccessDenied("Your account is Read only or pending confirmation; suggested edits are disabled.")
        return super().pair(name, project_ids, minutes=minutes, allow_suggestions=allow_suggestions,
                            confirmed=confirmed, confirmed_suggestions=confirmed_suggestions)
