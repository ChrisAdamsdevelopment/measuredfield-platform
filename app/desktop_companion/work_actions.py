"""Owner-consented filesystem operations confined to existing LocalConnect grants.

The model cannot create a folder grant or a Work session. The trusted paired
dispatcher supplies a live authorizer, which is rechecked before disk mutation.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable

from app.local_connect.store import AccessDenied, ConflictError, LocalConnect, MAX_FILE_BYTES


class WorkActions:
    """Atomic writes and directory creation in owner-granted project roots."""

    def __init__(self, local: LocalConnect, authorize: Callable[[str], None]):
        if not isinstance(local, LocalConnect) or not callable(authorize):
            raise AccessDenied("A verified owner work session is required")
        self.local = local
        self._authorize = authorize

    def _check(self, grant_id: str) -> None:
        self._authorize(grant_id)

    def write_file(self, grant_id: str, relative_path: str, content: str,
                   expected_sha256: str | None = None) -> dict:
        """Create or replace UTF-8, requiring old SHA-256 for replacement."""
        self._check(grant_id)
        if not isinstance(content, str):
            raise AccessDenied("File content must be UTF-8 text")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES or b"\x00" in encoded:
            raise AccessDenied("Text must be nonbinary and at most 256 KiB")
        if expected_sha256 is not None and (
                not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(c not in "0123456789abcdef" for c in expected_sha256)):
            raise AccessDenied("Expected SHA-256 must be 64 lowercase hex digits")
        path, relative = self.local._path(grant_id, relative_path, require_write=True)
        if not relative or not path.parent.is_dir():
            raise AccessDenied("Choose a filename in an approved existing directory")
        if path.exists() and not path.is_file():
            raise AccessDenied("Cannot overwrite a directory or special file")
        existed = path.exists()
        previous = hashlib.sha256(self.local._text_bytes(path)).hexdigest() if existed else None
        if existed and (expected_sha256 is None or previous != expected_sha256):
            raise ConflictError("File changed or missing expected baseline; read it first")
        if not existed and expected_sha256 is not None:
            raise ConflictError("Expected an existing file, but none was found")
        digest = hashlib.sha256(encoded).hexdigest()
        self._check(grant_id)
        self.local._path(grant_id, relative_path, require_write=True)
        if existed:
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", delete=False, dir=path.parent,
                    prefix=".mf-work-", suffix=".tmp",
                ) as out:
                    tmp = out.name
                    out.write(encoded)
                    out.flush()
                    os.fsync(out.fileno())
                self._check(grant_id)
                self.local._path(grant_id, relative_path, require_write=True)
                if not path.is_file() or (
                        hashlib.sha256(self.local._text_bytes(path)).hexdigest() != previous):
                    raise ConflictError("File changed during write; nothing replaced")
                os.replace(tmp, path)
                tmp = None
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(encoded)
                    out.flush()
                    os.fsync(out.fileno())
            except BaseException:
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
        with self.local._db() as db:
            self.local._audit(db, "work_file_write", "ok", grant_id, relative)
        return {"written": True, "path": relative, "sha256": digest,
                "created": not existed, "bytes": len(encoded)}

    def make_directory(self, grant_id: str, relative_path: str) -> dict:
        """Create exactly one directory beneath an approved project root."""
        self._check(grant_id)
        path, relative = self.local._path(grant_id, relative_path, require_write=True)
        if not relative or not path.parent.is_dir():
            raise AccessDenied("Choose a child directory of an existing approved folder")
        self._check(grant_id)
        self.local._path(grant_id, relative_path, require_write=True)
        try:
            os.mkdir(path)
            created = True
        except FileExistsError:
            # Another authorized worker may have just completed this exact
            # idempotent request. Verify the final object is STILL an ordinary
            # directory within the same approved grant; never accept a link.
            self._check(grant_id)
            resolved, _ = self.local._path(grant_id, relative_path, require_write=True)
            if not resolved.is_dir():
                raise AccessDenied("The destination is not a permitted directory") from None
            created = False
        with self.local._db() as db:
            self.local._audit(db, "work_directory_create", "ok", grant_id, relative)
        return {"created": created, "path": relative}
