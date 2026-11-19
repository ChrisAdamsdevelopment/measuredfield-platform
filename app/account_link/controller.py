"""Owner-side account linking, fail-closed local revocation and pairing permissions."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.local_connect import AccessDenied, LocalConnect, is_reparse_point
from app.local_connect_ui.pairing_controller import PairingOwnerController
from app.paired_connect.registry import PairingRegistry

from .client import DeviceApi, DeviceClientError, DeviceRevoked, resolve_trusted_origin
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
        # The saved origin is untrusted state, just like a caller-typed URL. If
        # it differs from the pinned installation origin (or is missing), do
        # not contact that server and never display its check number.
        if self._state["status"] in ("pending", "active", "unverified"):
            try:
                saved = self._state["server_url"]
                if not saved:
                    raise DeviceClientError("Saved workspace address is unavailable.")
                resolve_trusted_origin(saved)
            except DeviceClientError:
                self._revoke(
                    "The saved workspace address is not trusted. "
                    "Local connections were disconnected safely.")
        # Never restore an active permission from cached JSON without a live server check.
        if self._state["status"] == "active":
            self._state["status"] = "unverified"
        if self._state["status"] != "not_connected":
            self._stop_local_connections()

    def _credential_present(self) -> bool:
        try:
            return bool(self.secrets.load())
        except (SecureStoreUnavailable, OSError):
            # On a non-Windows test runner the DPAPI implementation is deliberately
            # unavailable; an absent file still represents a pristine local pilot.
            if isinstance(self.secrets, WindowsCredentialStore):
                return self.secrets.file.exists() or is_reparse_point(self.secrets.file)
            return True  # Unknown secret store failure is never permission to write.

    @staticmethod
    def _empty_state(*, unverified: bool = False) -> dict:
        return {"status": "unverified" if unverified else "not_connected",
                "server_url": "", "device_id": "", "access": "",
                "business": "", "name": ""}

    def _load(self) -> dict:
        if is_reparse_point(self.path):
            return self._empty_state(unverified=True)
        try:
            source = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty_state(unverified=self._credential_present())
        except (ValueError, OSError):
            return self._empty_state(unverified=True)
        if not isinstance(source, dict):
            return self._empty_state(unverified=True)
        allowed = ("status", "server_url", "device_id", "access", "business", "name")
        values = {k: source.get(k, "") for k in allowed}
        values = {k: v if isinstance(v, str) and len(v) <= 250 else "" for k, v in values.items()}
        if values["status"] not in ("not_connected", "pending", "active", "revoked", "unverified"):
            values["status"] = "unverified"
        if values["status"] == "not_connected" and self._credential_present():
            values["status"] = "unverified"
        if values["status"] in ("pending", "active") and (
                not values["server_url"] or not values["device_id"] or
                not self._credential_present()):
            values["status"] = "unverified"
        return values

    def _save(self) -> None:
        if is_reparse_point(self.path):
            raise AccessDenied("Account state cannot be a symbolic link.")
        # Whitelist fields so neither the pairing code nor device credential can be saved.
        fields = ("status", "server_url", "device_id", "access", "business", "name")
        payload = {key: self._state.get(key, "") for key in fields}
        temporary = self.home / "account_connection.tmp"
        if is_reparse_point(temporary):
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
            self._state["status"] == "active" and self._state["access"] in ("suggest", "work"))

    def can_work(self) -> bool:
        """Direct changes only when a real linked computer has Work permission."""
        return self._state["status"] == "active" and self._state["access"] == "work"

    def _stop_local_connections(self) -> None:
        # Revoke ALL local MCP pairing capabilities, including previously issued sessions.
        for row in self.registry.list():
            if row["status"] == "active":
                self.registry.revoke(row["pairing_id"])

    def _read_only_policy(self) -> None:
        status = self._state["status"]
        if status in ("pending", "unverified", "revoked"):
            self._stop_local_connections()
        elif status == "active":
            access = self._state["access"]
            for row in self.registry.list():
                if row["status"] != "active":
                    continue
                if ((access == "read" and row["can_suggest_edits"])
                        or (access != "work" and row.get("can_write", False))):
                    self.registry.revoke(row["pairing_id"])

    def _revoke(self, notice: str) -> AccountView:
        # First revoke in-memory permissions. DPAPI deletion may fail on Windows
        # (locked file), but must never preserve stale active / suggest permissions.
        self._state.update(status="revoked", access="", business="")
        self._check_number = ""
        self._notice = notice
        try:
            self._stop_local_connections()
        finally:
            try:
                self.secrets.clear()
            except (SecureStoreUnavailable, OSError):
                self._notice += " Protected credential cleanup requires attention."
            self._save()
        return self.view()

    def connect(self, server_url: str, code: str, device_name: str) -> AccountView:
        if self._state["status"] in ("active", "pending", "unverified"):
            raise AccessDenied("Disconnect the existing computer before pairing again.")
        trusted_url = resolve_trusted_origin(server_url)
        api = self.client_factory(trusted_url)
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
            try:
                self._read_only_policy()  # revoke old sessions before any fallible state write
                self._save()
            except (AccessDenied, OSError, SecureStoreUnavailable):
                self._state["status"] = "unverified"
                self._state["access"] = ""
                self._check_number = ""
                self._stop_local_connections()
                try:
                    self.secrets.clear()
                except (SecureStoreUnavailable, OSError):
                    pass
                try:
                    api.disconnect(intro.credential)
                except DeviceClientError:
                    pass
                raise AccessDenied("Computer setup could not be completed safely.") from None
            return self.view()
        finally:
            api.close()

    def refresh(self) -> AccountView:
        if self._state["status"] not in ("pending", "active", "unverified"):
            return self.view()
        try:
            credential = self.secrets.load()
        except (SecureStoreUnavailable, OSError):
            credential = None
        if not credential:
            return self._revoke("This computer has no valid saved credential and was disconnected locally.")
        # Never send the bearer to an address loaded from tampered/corrupt JSON.
        try:
            saved = self._state["server_url"]
            if not saved:
                raise DeviceClientError("Saved workspace address is unavailable.")
            trusted_url = resolve_trusted_origin(saved)
            api = self.client_factory(trusted_url)
        except DeviceClientError:
            return self._revoke(
                "The saved workspace address could not be trusted. "
                "Local trusted connections stopped.")
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
                trusted_url = resolve_trusted_origin(self._state["server_url"])
                api = self.client_factory(trusted_url)
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
             allow_suggestions: bool = False, allow_writes: bool = False,
             confirmed: bool = False, confirmed_suggestions: bool = False,
             confirmed_writes: bool = False) -> dict:
        state = self.account.view().status
        if state != "not_connected":
            # Re-check each account-linked pairing at the time of the consequential action.
            self.account.refresh()
            state = self.account.view().status
        if state not in ("not_connected", "active"):
            raise AccessDenied("This computer must be confirmed and connected before sharing folders.")
        if allow_suggestions and not self.account.can_suggest_edits():
            raise AccessDenied("Your account is Read only or pending confirmation; suggested edits are disabled.")
        if allow_writes and not self.account.can_work():
            raise AccessDenied("This computer must have owner-approved Work mode before direct edits.")
        return super().pair(name, project_ids, minutes=minutes, allow_suggestions=allow_suggestions,
                            allow_writes=allow_writes, confirmed=confirmed,
                            confirmed_suggestions=confirmed_suggestions,
                            confirmed_writes=confirmed_writes)
