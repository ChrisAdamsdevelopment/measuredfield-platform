"""Owner-local pairing controls. Never import this in a model-callable MCP tool.

The owner UI passes approved folder IDs and confirmation; a pair is a local
temporary capability, not proof of ChatGPT account or remote user identity.
"""
from __future__ import annotations

import json
import sqlite3

from app.local_connect import AccessDenied, LocalConnect
from app.paired_connect.registry import PairingRegistry


class PairingOwnerController:
    """UI-facing adapter over the same owner-private LocalConnect state."""

    def __init__(self, store: LocalConnect | None = None):
        self.store = store or LocalConnect()
        self.registry = PairingRegistry(self.store.home)

    def available_projects(self) -> list[dict]:
        return self.store.grants(include_roots=True)

    def connections(self) -> list[dict]:
        # Never include token or token hash or local filesystem roots in sessions.
        return self.registry.list()

    def pair(self, name: str, project_ids: list[str], *,
             minutes: int = 15, allow_suggestions: bool = False, allow_writes: bool = False,
             confirmed: bool = False, confirmed_suggestions: bool = False,
             confirmed_writes: bool = False) -> dict:
        if not confirmed:
            raise AccessDenied("Confirm this trusted connection before pairing")
        if not isinstance(name, str) or not name.strip():
            raise AccessDenied("Choose a name for this connection")
        if allow_suggestions and not confirmed_suggestions:
            raise AccessDenied("Confirm permission for suggested changes separately")
        if allow_writes and not confirmed_writes:
            raise AccessDenied("Confirm direct Work-mode file changes separately")
        if not isinstance(minutes, int) or isinstance(minutes, bool):
            raise AccessDenied("Choose an expiration in minutes")
        return self.registry.create(
            name, project_ids, expires_minutes=minutes,
            allow_proposals=allow_suggestions, allow_writes=allow_writes,
            owner_confirmed=True, owner_confirmed_write=confirmed_writes,
        )

    def disconnect(self, pairing_id: str, *, confirmed: bool = False) -> bool:
        if not confirmed:
            raise AccessDenied("Confirm before disconnecting this session")
        return self.registry.revoke(pairing_id)

    def connection_projects(self, pairing_id: str) -> list[dict]:
        """Owner-only detail view, never exposed through paired MCP tools.

        The registry's public connection list intentionally omits local roots.
        Only the on-device owner window resolves names/paths for inspection.
        """
        if not any(c["pairing_id"] == pairing_id for c in self.registry.list()):
            raise AccessDenied("Unknown connection")
        db = sqlite3.connect(self.registry.db_path, timeout=10)
        try:
            row = db.execute(
                "SELECT grants_json,allow_proposals,allow_writes FROM pairings WHERE id=?",
                (pairing_id,),
            ).fetchone()
        finally:
            db.close()
        if row is None:
            raise AccessDenied("Unknown connection")
        selected, allows_suggestions, allows_writes = json.loads(row[0]), bool(row[1]), bool(row[2])
        current = {g["id"]: g for g in self.available_projects()}
        projects = []
        for grant_id in selected:
            grant = current.get(grant_id)
            if not grant:
                projects.append({
                    "id": grant_id, "name": "Disconnected folder",
                    "root": "(No longer accessible)", "access": "Disconnected",
                })
            else:
                projects.append({
                    "id": grant_id, "name": grant["name"], "root": grant["root"],
                    "access": (
                        "Direct Work-mode edits"
                        if allows_writes and grant["access"] == "readwrite"
                        else "Suggest edits + read"
                        if allows_suggestions and grant["access"] == "readwrite"
                        else "Read only"
                    ),
                })
        return projects

    def pairing_activity(self) -> list[dict]:
        return self.registry.audit()
