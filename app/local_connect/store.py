"""Owner-controlled local project grants and audited change proposals.

No network, shell execution, or model-callable grant/approve functionality.
This is a private developer-stage companion, NOT the cloud OAuth bridge.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MAX_FILE_BYTES = 256 * 1024
MAX_LIST_ITEMS = 100
BLOCKED_DIRS = frozenset({
    ".git", ".ssh", ".aws", ".azure", ".gnupg", ".config", ".kube",
    ".venv", "venv", "node_modules", "__pycache__", "secrets",
    "credentials", "private_keys", "private-keys", ".terraform",
})
BLOCKED_FILES = frozenset({
    "id_rsa", "id_ed25519", "known_hosts", "credentials.json",
    "service-account.json", "service_account.json", "token.json",
    "secrets.json", "passwords.txt", "settings.local.json",
})
BLOCKED_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".p8", ".kdbx", ".jks",
    ".ovpn", ".ppk", ".asc", ".gpg",
)
DEVICE_NAME_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)


class AccessDenied(ValueError):
    """The requested action is not covered by the owner's explicit grant."""


class ConflictError(ValueError):
    """The file was changed since the proposed edit; owner review is required again."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sensitive(name: str) -> bool:
    lower = name.casefold()
    return (
        lower in BLOCKED_DIRS
        or lower in BLOCKED_FILES
        or lower == ".env"
        or lower.startswith(".env.")
        or lower.endswith(BLOCKED_SUFFIXES)
        or lower.startswith("id_rsa.")
        or lower.startswith("id_ed25519.")
    )


def _reparse(path: Path) -> bool:
    """Deny symbolic links, Windows junctions and reparse points."""
    try:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except FileNotFoundError:
        return False


def _all_parents_safe(path: Path) -> None:
    for part in (path, *path.parents):
        if _reparse(part):
            raise AccessDenied("Linked or redirected folders are not allowed")


def _relative_parts(relative_path: str) -> list[str]:
    if not isinstance(relative_path, str):
        raise AccessDenied("A relative project path is required")
    if relative_path in ("", "."):
        return []
    if (
        relative_path.startswith(("/", "\\"))
        or ":" in relative_path
        or "\x00" in relative_path
    ):
        raise AccessDenied("Absolute, drive and device paths are not allowed")
    raw = relative_path.replace("\\", "/")
    pieces = raw.split("/")
    for piece in pieces:
        if (
            piece in ("", ".", "..")
            or piece.rstrip(" .") != piece
            or any(ord(c) < 32 for c in piece)
            or DEVICE_NAME_RE.fullmatch(piece)
        ):
            raise AccessDenied("Invalid project path segment")
        if _sensitive(piece):
            raise AccessDenied("Protected or credential-like paths are not available")
    return pieces


def _home() -> Path:
    configured = os.environ.get("MEASUREDFIELD_LOCAL_CONNECT_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    parent = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return parent / "MeasuredField" / "ComputerConnect"


class LocalConnect:
    """SQLite-backed owner grants with transaction-protected pending edits."""

    def __init__(self, home: Path | str | None = None) -> None:
        self.home = Path(home).expanduser().resolve() if home else _home().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        _all_parents_safe(self.home)
        self.db_path = self.home / "local_connect.sqlite3"
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS grants (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL,
                  access TEXT NOT NULL CHECK(access IN ('read','readwrite')),
                  active INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS proposals (
                  id TEXT PRIMARY KEY, grant_id TEXT NOT NULL,
                  relative_path TEXT NOT NULL, baseline_hash TEXT,
                  existed INTEGER NOT NULL, new_content TEXT NOT NULL,
                  new_hash TEXT NOT NULL, status TEXT NOT NULL,
                  created_at TEXT NOT NULL, decided_at TEXT,
                  FOREIGN KEY(grant_id) REFERENCES grants(id)
                );
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                  action TEXT NOT NULL, outcome TEXT NOT NULL,
                  grant_id TEXT, relative_path TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_proposals_status ON proposals(status);
                """
            )
        if os.name != "nt":
            os.chmod(self.home, 0o700)
            os.chmod(self.db_path, 0o600)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """Commit/rollback a transaction and always release the OS database handle."""
        db = sqlite3.connect(str(self.db_path), timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _audit(db: sqlite3.Connection, action: str, outcome: str,
               grant_id: str | None = None, path: str | None = None) -> None:
        db.execute(
            "INSERT INTO audit(at,action,outcome,grant_id,relative_path) VALUES(?,?,?,?,?)",
            (_now(), action, outcome, grant_id, path[:180] if path else None),
        )

    def grant(self, name: str, root: str | Path, access: str = "read") -> dict[str, Any]:
        if access not in {"read", "readwrite"}:
            raise AccessDenied("Grant access must be read or readwrite")
        name = str(name).strip()
        if not 1 <= len(name) <= 100:
            raise AccessDenied("Project name must be 1 to 100 characters")
        raw = Path(root).expanduser()
        if not raw.is_absolute() or not raw.is_dir():
            raise AccessDenied("Choose an existing absolute project directory")
        if str(raw).startswith(("\\\\", "//")):
            raise AccessDenied("Network shares are not supported in the local pilot")
        _all_parents_safe(raw)
        resolved = raw.resolve(strict=True)
        if resolved == Path(resolved.anchor):
            raise AccessDenied("Grant a project folder, not an entire drive")
        if resolved == self.home or resolved in self.home.parents or self.home in resolved.parents:
            raise AccessDenied("The companion's private state cannot be shared")
        for segment in resolved.parts[1:]:
            if _sensitive(segment):
                raise AccessDenied("Credential or private folders cannot be granted")
        grant_id = secrets.token_hex(12)
        with self._db() as db:
            db.execute(
                "INSERT INTO grants(id,name,root,access,active,created_at) VALUES(?,?,?,?,1,?)",
                (grant_id, name, str(resolved), access, _now()),
            )
            self._audit(db, "grant", "approved", grant_id)
        return {"id": grant_id, "name": name, "root": str(resolved), "access": access}

    def grants(self, include_roots: bool = False) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id,name,root,access,created_at FROM grants WHERE active=1 ORDER BY name"
            ).fetchall()
        return [
            {key: row[key] for key in
             (("id", "name", "root", "access", "created_at") if include_roots else
              ("id", "name", "access", "created_at"))}
            for row in rows
        ]

    def revoke(self, grant_id: str) -> bool:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE grants SET active=0,revoked_at=? WHERE id=? AND active=1",
                (_now(), grant_id),
            )
            if result.rowcount:
                db.execute(
                    "UPDATE proposals SET status='revoked',decided_at=? "
                    "WHERE grant_id=? AND status='pending'", (_now(), grant_id),
                )
            self._audit(db, "revoke", "approved" if result.rowcount else "not_found", grant_id)
            return bool(result.rowcount)

    def _path(self, grant_id: str, relative_path: str,
              require_write: bool = False) -> tuple[Path, str]:
        parts = _relative_parts(relative_path)
        with self._db() as db:
            row = db.execute(
                "SELECT root,access,active FROM grants WHERE id=?", (grant_id,)
            ).fetchone()
        if not row or not row["active"]:
            raise AccessDenied("Project permission missing or revoked")
        if require_write and row["access"] != "readwrite":
            raise AccessDenied("This project was granted read-only access")
        root = Path(row["root"])
        if not root.is_dir():
            raise AccessDenied("Approved project folder is unavailable")
        _all_parents_safe(root)
        path = root.joinpath(*parts)
        # Check every existing component, including the final file or directory.
        current = root
        for piece in parts:
            current = current / piece
            if _reparse(current):
                raise AccessDenied("Linked or redirected folders are not allowed")
        target = path.resolve(strict=False)
        if os.path.normcase(os.path.commonpath((str(root.resolve()), str(target)))) != os.path.normcase(str(root.resolve())):
            raise AccessDenied("Project path escapes the approved folder")
        return path, "/".join(parts)

    def browse(self, grant_id: str, relative_path: str = "") -> dict[str, Any]:
        folder, normalized = self._path(grant_id, relative_path)
        if not folder.is_dir():
            raise AccessDenied("Requested project directory does not exist")
        files = []
        for child in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            if _sensitive(child.name) or _reparse(child):
                continue
            try:
                st = child.stat()
            except OSError:
                continue
            files.append({
                "name": child.name, "kind": "directory" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else None,
            })
            if len(files) == MAX_LIST_ITEMS:
                break
        with self._db() as db:
            self._audit(db, "browse", "ok", grant_id, normalized)
        return {"project_id": grant_id, "path": normalized,
                "items": files, "capped": len(files) == MAX_LIST_ITEMS}

    def _text_bytes(self, path: Path) -> bytes:
        if not path.is_file() or _reparse(path):
            raise AccessDenied("File is missing or is not a regular file")
        with path.open("rb") as fp:
            raw = fp.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise AccessDenied("File exceeds the 256 KiB text limit")
        if b"\x00" in raw:
            raise AccessDenied("Binary files are not supported")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AccessDenied("Only valid UTF-8 text files are supported") from exc
        return raw

    def read(self, grant_id: str, relative_path: str) -> dict[str, Any]:
        path, normalized = self._path(grant_id, relative_path)
        if not normalized:
            raise AccessDenied("Choose a file, not the root directory")
        content = self._text_bytes(path)
        with self._db() as db:
            self._audit(db, "read", "ok", grant_id, normalized)
        return {"project_id": grant_id, "path": normalized,
                "content": content.decode("utf-8"), "sha256": _sha(content)}

    def propose(self, grant_id: str, relative_path: str, new_content: str) -> dict[str, Any]:
        if not isinstance(new_content, str):
            raise AccessDenied("A UTF-8 text proposal is required")
        encoded = new_content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES or b"\x00" in encoded:
            raise AccessDenied("Proposal is binary or exceeds 256 KiB")
        path, normalized = self._path(grant_id, relative_path, require_write=True)
        if not normalized or not path.parent.is_dir():
            raise AccessDenied("Choose a filename inside an existing project folder")
        if path.exists() and not path.is_file():
            raise AccessDenied("Cannot replace a directory or special file")
        existed = path.exists()
        baseline = _sha(self._text_bytes(path)) if existed else None
        proposal_id = secrets.token_hex(16)
        with self._db() as db:
            db.execute(
                "INSERT INTO proposals(id,grant_id,relative_path,baseline_hash,existed,"
                "new_content,new_hash,status,created_at) VALUES(?,?,?,?,?,?,?,'pending',?)",
                (proposal_id, grant_id, normalized, baseline, int(existed),
                 new_content, _sha(encoded), _now()),
            )
            self._audit(db, "propose_edit", "pending_owner_review", grant_id, normalized)
        return {"proposal_id": proposal_id, "project_id": grant_id, "path": normalized,
                "old_sha256": baseline, "new_sha256": _sha(encoded),
                "owner_approval_required": True, "file_existed": existed}

    def pending(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT p.id,p.grant_id,p.relative_path,p.existed,p.baseline_hash,"
                "p.new_hash,p.created_at,g.name FROM proposals p "
                "JOIN grants g ON g.id=p.grant_id "
                "WHERE p.status='pending' AND g.active=1 ORDER BY p.created_at"
            ).fetchall()
        return [{
            "proposal_id": row["id"], "project_id": row["grant_id"],
            "project": row["name"], "path": row["relative_path"],
            "file_existed": bool(row["existed"]), "old_sha256": row["baseline_hash"],
            "new_sha256": row["new_hash"], "created_at": row["created_at"],
        } for row in rows]

    def _proposal(self, proposal_id: str) -> sqlite3.Row:
        with self._db() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise AccessDenied("Unknown change proposal")
        return row

    def diff(self, proposal_id: str) -> str:
        row = self._proposal(proposal_id)
        if row["status"] != "pending":
            raise AccessDenied("Proposal is no longer pending")
        path, normalized = self._path(row["grant_id"], row["relative_path"], True)
        before = self._text_bytes(path).decode("utf-8") if path.exists() else ""
        lines = list(difflib.unified_diff(
            before.splitlines(keepends=True), row["new_content"].splitlines(keepends=True),
            fromfile="current/" + normalized, tofile="proposed/" + normalized,
        ))
        return "".join(lines)

    def reject(self, proposal_id: str) -> bool:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT grant_id,relative_path FROM proposals WHERE id=? AND status='pending'",
                             (proposal_id,)).fetchone()
            if not row:
                return False
            db.execute("UPDATE proposals SET status='rejected',decided_at=? WHERE id=?",
                       (_now(), proposal_id))
            self._audit(db, "reject_edit", "rejected", row["grant_id"], row["relative_path"])
            return True

    def approve(self, proposal_id: str) -> dict[str, Any]:
        """ONLY the separate local owner CLI exposes this; MCP never calls it."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM proposals WHERE id=? AND status='pending'",
                             (proposal_id,)).fetchone()
            if not row:
                raise AccessDenied("Change proposal is absent, revoked or already decided")
            path, normalized = self._path(row["grant_id"], row["relative_path"], True)
            # This rechecks a live grant and all path components immediately before writing.
            if not path.parent.is_dir():
                raise AccessDenied("Target folder no longer exists")
            existed = path.exists()
            if existed != bool(row["existed"]):
                raise ConflictError("File existence changed since proposal was staged")
            if existed and _sha(self._text_bytes(path)) != row["baseline_hash"]:
                raise ConflictError("File contents changed since proposal was staged")
            content = row["new_content"].encode("utf-8")
            if _sha(content) != row["new_hash"]:
                raise ConflictError("Stored change content does not match its hash")
            if existed:
                tmp_name = None
                try:
                    with tempfile.NamedTemporaryFile(mode="wb", delete=False, dir=path.parent,
                                                     prefix=".mf-proposal-", suffix=".tmp") as out:
                        tmp_name = out.name
                        out.write(content)
                        out.flush()
                        os.fsync(out.fileno())
                    # One more path/hash check before the final atomic replacement.
                    self._path(row["grant_id"], row["relative_path"], True)
                    if _sha(self._text_bytes(path)) != row["baseline_hash"]:
                        raise ConflictError("File changed during owner approval")
                    os.replace(tmp_name, path)
                    tmp_name = None
                finally:
                    if tmp_name and os.path.exists(tmp_name):
                        os.unlink(tmp_name)
            else:
                # O_EXCL prevents silently replacing a file created after approval began.
                self._path(row["grant_id"], row["relative_path"], True)
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(fd, "wb") as out:
                        out.write(content)
                        out.flush()
                        os.fsync(out.fileno())
                except BaseException:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    raise
            db.execute("UPDATE proposals SET status='approved',decided_at=? WHERE id=?",
                       (_now(), proposal_id))
            self._audit(db, "apply_edit", "owner_approved", row["grant_id"], normalized)
            return {"applied": True, "path": normalized, "sha256": row["new_hash"]}

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 100)
        with self._db() as db:
            rows = db.execute(
                "SELECT at,action,outcome,grant_id,relative_path FROM audit "
                "ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
