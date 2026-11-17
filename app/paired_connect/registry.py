"""Owner-only private pilot pairing registry for a capability-scoped local MCP server.

A tunnel transports MCP; it DOES NOT authenticate the end user. This registry only
binds a local owner-approved capability to a specific stdio server process, not
to a verified ChatGPT/OAuth identity. Never publish this as multi-tenant auth.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.local_connect import AccessDenied, LocalConnect

MAX_MINUTES = 24 * 60
MAX_GRANTS = 8


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_id(value: str) -> str:
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise AccessDenied("Unknown or inactive connection")
    return value


class PairingRegistry:
    def __init__(self, home: Path | str | None = None):
        self.connect = LocalConnect(home)
        self.home = self.connect.home
        self.db_path = self.home / "paired_connections.sqlite3"
        self.token_dir = self.home / "paired_connection_tokens"
        self.token_dir.mkdir(exist_ok=True)
        if self.token_dir.is_symlink() or (
            hasattr(self.token_dir, "is_junction") and self.token_dir.is_junction()
        ):
            raise AccessDenied("Pairing token storage must not be a link")
        if os.name != "nt":
            os.chmod(self.token_dir, 0o700)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pairings(
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    grants_json TEXT NOT NULL,
                    allow_proposals INTEGER NOT NULL DEFAULT 0,
                    token_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS pairing_audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    pairing_id TEXT,
                    operation TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    grant_id TEXT
                );
            """)
        if os.name != "nt":
            os.chmod(self.db_path, 0o600)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            with db:
                yield db
        finally:
            db.close()

    def _log(self, action: str, result: str, pair_id: str | None = None,
             grant_id: str | None = None) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO pairing_audit(timestamp,pairing_id,operation,outcome,grant_id) "
                "VALUES(?,?,?,?,?)",
                (time.time(), pair_id, action, result, grant_id)
            )

    def _token_file(self, pair_id: str) -> Path:
        return self.token_dir / (_validate_id(pair_id) + ".token")

    def create(self, label: str, grant_ids: list[str], *, owner_confirmed: bool = False,
               allow_proposals: bool = False, expires_minutes: int = 15) -> dict:
        """Explicit OWNER invocation only, never part of the MCP tool surface."""
        if not owner_confirmed:
            raise AccessDenied("The owner must approve pairing locally")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
            raise AccessDenied("Choose a connection name (1–80 characters)")
        if (not isinstance(expires_minutes, int) or isinstance(expires_minutes, bool)
                or not 1 <= expires_minutes <= MAX_MINUTES):
            raise AccessDenied("Connections must expire between 1 minute and 24 hours")
        if (
            not isinstance(grant_ids, list)
            or not 1 <= len(grant_ids) <= MAX_GRANTS
            or not all(isinstance(g, str) and g for g in grant_ids)
            or len(set(grant_ids)) != len(grant_ids)
        ):
            raise AccessDenied("Choose 1–8 distinct approved project folders")
        grants = {g["id"]: g for g in self.connect.grants()}
        for grant_id in grant_ids:
            if grant_id not in grants:
                raise AccessDenied("Cannot pair a disconnected project")
        if allow_proposals and not any(grants[g]["access"] == "readwrite" for g in grant_ids):
            raise AccessDenied("No selected folder permits proposed edits")
        pair_id = secrets.token_hex(16)
        token = secrets.token_urlsafe(48)
        created = time.time()
        token_file = self._token_file(pair_id)
        # The secret never enters a command line, a tool response, or an audit row.
        fd = os.open(str(token_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as out:
                out.write(token)
                out.flush()
                os.fsync(out.fileno())
            if os.name != "nt":
                os.chmod(token_file, 0o600)
            with self._db() as db:
                db.execute(
                    "INSERT INTO pairings(id,label,grants_json,allow_proposals,token_hash,"
                    "created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                    (pair_id, label.strip(), json.dumps(grant_ids, separators=(",", ":")),
                     int(allow_proposals), _hash_token(token), created,
                     created + expires_minutes * 60),
                )
        except BaseException:
            token_file.unlink(missing_ok=True)
            raise
        self._log("pair", "owner_confirmed", pair_id)
        return {"pairing_id": pair_id, "name": label.strip(),
                "project_count": len(grant_ids),
                "read_only": not allow_proposals,
                "expires_at": _iso(created + expires_minutes * 60)}

    def list(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id,label,grants_json,allow_proposals,created_at,expires_at,revoked_at "
                "FROM pairings ORDER BY created_at DESC"
            ).fetchall()
        now = time.time()
        return [
            {"pairing_id": row["id"], "name": row["label"],
             "project_count": len(json.loads(row["grants_json"])),
             "can_suggest_edits": bool(row["allow_proposals"]),
             "status": "revoked" if row["revoked_at"] is not None
                       else "expired" if row["expires_at"] <= now else "active",
             "expires_at": _iso(row["expires_at"])}
            for row in rows
        ]

    def revoke(self, pair_id: str) -> bool:
        pair_id = _validate_id(pair_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE pairings SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (time.time(), pair_id)
            ).rowcount
        # DB revocation is the real boundary, even if token file removal fails.
        try:
            self._token_file(pair_id).unlink(missing_ok=True)
        except OSError:
            pass
        self._log("revoke", "revoked" if changed else "no_active_pair", pair_id)
        return bool(changed)

    def token_for_local_launcher(self, pair_id: str) -> str:
        """Private trusted owner process only. Never return this from an MCP tool."""
        file = self._token_file(pair_id)
        if file.is_symlink() or (
            hasattr(file, "is_junction") and file.is_junction()
        ):
            raise AccessDenied("Invalid private connection")
        try:
            token = file.read_text(encoding="ascii")
        except (FileNotFoundError, OSError):
            raise AccessDenied("Connection is not available") from None
        if not token or len(token) > 128:
            raise AccessDenied("Connection secret is invalid")
        # Verify status and token before launching any MCP tools.
        self.authorize(pair_id, token)
        return token

    def authorize(self, pair_id: str, token: str,
                  grant_id: str | None = None, action: str = "list") -> dict:
        if action not in {"list", "read", "propose"}:
            raise AccessDenied("This operation is not permitted")
        pair_id = _validate_id(pair_id)
        if not isinstance(token, str) or len(token) < 32 or len(token) > 128:
            raise AccessDenied("Invalid connection credentials")
        with self._db() as db:
            row = db.execute(
                "SELECT token_hash,grants_json,allow_proposals,expires_at,revoked_at "
                "FROM pairings WHERE id=?", (pair_id,)
            ).fetchone()
        if (not row or row["revoked_at"] is not None or row["expires_at"] <= time.time()
                or not hmac.compare_digest(row["token_hash"], _hash_token(token))):
            self._log("authorize", "denied", pair_id, grant_id)
            raise AccessDenied("Connection expired, revoked or unauthorized")
        allowed = tuple(json.loads(row["grants_json"]))
        current = {g["id"]: g for g in self.connect.grants()}
        live = [g for g in allowed if g in current]
        if grant_id is not None and (grant_id not in live):
            self._log("authorize", "denied", pair_id, grant_id)
            raise AccessDenied("That folder is not approved for this connection")
        if action == "propose":
            if not row["allow_proposals"] or not grant_id or current[grant_id]["access"] != "readwrite":
                self._log("authorize", "denied", pair_id, grant_id)
                raise AccessDenied("Proposed edits are not allowed in this connection")
        return {
            "pairing_id": pair_id,
            "grants": tuple(live),
            "can_propose": bool(row["allow_proposals"]),
        }

    def audit(self, limit: int = 30) -> list[dict]:
        if not isinstance(limit, int):
            raise AccessDenied("Invalid audit limit")
        with self._db() as db:
            rows = db.execute(
                "SELECT timestamp,pairing_id,operation,outcome,grant_id FROM pairing_audit "
                "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return [
            {"timestamp": _iso(r["timestamp"]), "pairing_id": r["pairing_id"],
             "operation": r["operation"], "outcome": r["outcome"],
             "project_id": r["grant_id"]}
            for r in rows
        ]


class BoundSession:
    """Enforce a particular owner-paired capability on every MCP tool call."""

    def __init__(self, pairing_id: str, token: str, home: Path | str | None = None):
        self.pairing_id = pairing_id
        self._token = token
        self.registry = PairingRegistry(home)
        self.registry.authorize(pairing_id, token)

    def _verify(self, grant_id: str | None = None, action: str = "list") -> dict:
        # Recheck BOTH capabilities for each tool, even when the Windows UI has
        # exited. A local pairing still proves which folder was approved; it is
        # NOT evidence that the owner's cloud account still permits access.
        local = self.registry.authorize(self.pairing_id, self._token, grant_id, action)
        from app.account_link.runtime_guard import AccountRuntimeGuard
        AccountRuntimeGuard(self.registry.home, registry=self.registry).check(action=action)
        return local

    def projects(self) -> list[dict]:
        session = self._verify()
        allowed = set(session["grants"])
        return [p for p in self.registry.connect.grants() if p["id"] in allowed]

    def browse(self, grant_id: str, relative_path: str = "") -> dict:
        self._verify(grant_id, "read")
        return self.registry.connect.browse(grant_id, relative_path)

    def read(self, grant_id: str, relative_path: str) -> dict:
        self._verify(grant_id, "read")
        return self.registry.connect.read(grant_id, relative_path)

    def propose(self, grant_id: str, relative_path: str, new_content: str) -> dict:
        self._verify(grant_id, "propose")
        return self.registry.connect.propose(grant_id, relative_path, new_content)

    def pending(self) -> list[dict]:
        session = self._verify()
        allowed = set(session["grants"])
        if not session["can_propose"]:
            return []
        return [p for p in self.registry.connect.pending() if p["project_id"] in allowed]

    def activity(self, limit: int = 20) -> list[dict]:
        session = self._verify()
        allowed = set(session["grants"])
        # Do not reveal activity for other folders to this pair.
        limit = max(1, min(int(limit), 100))
        return [entry for entry in self.registry.connect.audit(100)
                if entry["grant_id"] in allowed][:limit]
