"""Windows-DPAPI-only local credential storage. No plaintext fallback anywhere."""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path


class SecureStoreUnavailable(RuntimeError):
    """Secure Windows credential storage is not available."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise SecureStoreUnavailable("This device needs Windows-protected credential storage.")
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        entry = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        entry.argtypes = ([ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(_Blob)] if decrypt else
                          [ctypes.POINTER(_Blob), ctypes.c_wchar_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(_Blob)])
        entry.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        buffer = ctypes.create_string_buffer(data)
        original = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = _Blob()
        # CRYPTPROTECT_UI_FORBIDDEN (0x1) avoids unexpected interactive prompts.
        if not entry(ctypes.byref(original), None, None, None, None, 0x1, ctypes.byref(result)):
            raise SecureStoreUnavailable("Windows could not protect this computer credential.")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))
    except SecureStoreUnavailable:
        raise
    except (OSError, AttributeError, TypeError, ValueError):
        raise SecureStoreUnavailable("Windows credential protection is unavailable.") from None


class WindowsCredentialStore:
    """A per-Windows-user DPAPI blob, never an unprotected token file."""

    def __init__(self, home: str | Path):
        self.home = Path(home).expanduser().resolve()
        self.file = self.home / "account_device.dpapi"

    def _safe(self) -> None:
        if sys.platform != "win32":
            raise SecureStoreUnavailable("Windows credential protection is required.")
        self.home.mkdir(parents=True, exist_ok=True)
        if self.home.is_symlink() or self.file.is_symlink():
            raise SecureStoreUnavailable("Credential location cannot be a symbolic link.")

    def save(self, credential: str) -> None:
        self._safe()
        if not isinstance(credential, str) or not credential.startswith("mfd1.") or len(credential) > 300:
            raise SecureStoreUnavailable("Computer credential was not accepted.")
        blob = _dpapi(credential.encode("utf-8"), decrypt=False)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.home, prefix=".account-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.file)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self) -> str | None:
        self._safe()
        if not self.file.exists():
            return None
        if self.file.stat().st_size > 4096:
            raise SecureStoreUnavailable("Stored computer credential is invalid.")
        try:
            decoded = _dpapi(self.file.read_bytes(), decrypt=True).decode("utf-8")
        except (UnicodeError, OSError):
            raise SecureStoreUnavailable("Stored computer credential cannot be read.") from None
        if not decoded.startswith("mfd1.") or len(decoded) > 300:
            raise SecureStoreUnavailable("Stored computer credential is invalid.")
        return decoded

    def clear(self) -> None:
        self._safe()
        self.file.unlink(missing_ok=True)
