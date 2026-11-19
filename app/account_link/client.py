"""Typed HTTP client for the first-party MF-032 device API; no public tunnelling."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any
from urllib.parse import urlsplit
import ipaddress

import httpx


class DeviceClientError(RuntimeError):
    """User-safe failure message. Never contains a credential or raw HTTP response."""


class DeviceRevoked(DeviceClientError):
    """The remote device credential was revoked or is not recognized."""


@dataclass(frozen=True)
class DeviceStatus:
    device_id: str
    status: str
    access: str
    business: str
    name: str


@dataclass(frozen=True)
class PairingIntro:
    device_id: str
    credential: str = field(repr=False)  # Never print a device bearer in dataclass debugging.
    check_number: str
    confirm_by: str


def validate_origin(origin: str) -> str:
    if not isinstance(origin, str) or len(origin) > 250:
        raise DeviceClientError("Enter a valid MeasuredField workspace address.")
    parsed = urlsplit(origin)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise DeviceClientError("Use only the workspace origin, without a path or sign-in details.")
    host = (parsed.hostname or "").lower()
    if not host or parsed.scheme not in ("https", "http"):
        raise DeviceClientError("A secure MeasuredField workspace address is required.")
    try:
        ip = ipaddress.ip_address(host)
        loopback = ip.is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parsed.scheme == "http" and not loopback:
        raise DeviceClientError("HTTPS is required except for this computer's local test server.")
    try:
        _ = parsed.port
    except ValueError:
        raise DeviceClientError("Invalid port in workspace address.") from None
    return origin.rstrip("/")


def _is_loopback_url(origin: str) -> bool:
    host = (urlsplit(origin).hostname or "").lower()
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def configured_workspace() -> str | None:
    """Trust comes from managed install configuration, NOT owner-typed URL or cached JSON.

    No production/default hostname can be guessed safely; an installation must
    explicitly pin its real origin before device codes may be exchanged.
    """
    configured = os.environ.get("MEASUREDFIELD_WORKSPACE_URL", "").strip()
    if not configured:
        return None
    origin = validate_origin(configured)
    if not origin.startswith("https://") or _is_loopback_url(origin):
        raise DeviceClientError("The installed MeasuredField workspace configuration is invalid.")
    return origin


def developer_mode_enabled() -> bool:
    return os.environ.get("MEASUREDFIELD_DEVELOPER_MODE", "").strip() == "1"


def resolve_trusted_origin(requested: str) -> str:
    """Before ANY network call: pin HTTPS, or explicit local-only development mode."""
    configured = configured_workspace()
    if configured is not None:
        if requested:
            supplied = validate_origin(requested)
            if supplied != configured:
                raise DeviceClientError(
                    "This address is not the trusted MeasuredField workspace. Connection refused.")
        return configured

    if developer_mode_enabled():
        origin = validate_origin(requested)
        if not _is_loopback_url(origin):
            raise DeviceClientError(
                "Developer mode accepts only a service running on this same computer.")
        return origin

    raise DeviceClientError(
        "A trusted MeasuredField workspace address has not been installed on this computer.")


class DeviceApi:
    def __init__(self, origin: str, *, client: Any = None):
        self.origin = resolve_trusted_origin(origin)
        self._client = client if client is not None else httpx.Client(
            base_url=self.origin, timeout=httpx.Timeout(6.0), follow_redirects=False,
            trust_env=False)
        self._own_client = client is None

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def _request(self, method: str, path: str, *, credential: str | None = None,
                 payload: dict | None = None) -> dict:
        headers = {"Accept": "application/json"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        try:
            request = self._client.get if method == "GET" else self._client.post
            response = request(path, headers=headers, **({"json": payload} if payload is not None else {}))
        except (httpx.HTTPError, OSError, RuntimeError):
            raise DeviceClientError("Cannot reach the MeasuredField workspace right now.") from None
        if response.status_code == 401:
            raise DeviceRevoked("This computer is disconnected. Connect it again to continue.")
        if response.status_code in (301, 302, 303, 307, 308):
            raise DeviceClientError("Workspace address redirected unexpectedly; connection stopped.")
        if response.status_code == 403:
            raise DeviceClientError("The owner must confirm this computer in the workspace first.")
        if response.status_code == 429:
            raise DeviceClientError("Too many requests. Please try again shortly.")
        if response.status_code not in (200, 201):
            raise DeviceClientError("The workspace declined this request. Check the code or account status.")
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise DeviceClientError("The workspace returned an unreadable response.") from None

    def pair(self, code: str, device_name: str) -> PairingIntro:
        import sys
        if not isinstance(code, str) or not 4 <= len(code) <= 24:
            raise DeviceClientError("Enter the short code shown in your workspace.")
        value = self._request("POST", "/api/device/v1/pair", payload={
            "code": code, "device_name": device_name[:120],
            "platform": "windows" if sys.platform == "win32" else "linux"})
        if (value.get("status") != "pending" or not isinstance(value.get("credential"), str)
                or not value["credential"].startswith("mfd1.")
                or not isinstance(value.get("check_number"), str)
                or len(value["check_number"]) != 6 or not value["check_number"].isdigit()
                or not isinstance(value.get("device_id"), str)):
            raise DeviceClientError("The workspace returned an invalid pairing confirmation.")
        return PairingIntro(value["device_id"], value["credential"],
                            value["check_number"], str(value.get("confirm_by", "")))

    def status(self, credential: str) -> DeviceStatus:
        value = self._request("GET", "/api/device/v1/status", credential=credential)
        if (value.get("status") not in ("pending", "active") or
                value.get("access") not in ("read", "suggest", "work") or
                not isinstance(value.get("device_id"), str)):
            raise DeviceClientError("Unexpected computer status. No file action was taken.")
        return DeviceStatus(value["device_id"], value["status"], value["access"],
                            str(value.get("business") or "")[:120], str(value.get("name") or "")[:120])

    def heartbeat(self, credential: str) -> DeviceStatus:
        self._request("POST", "/api/device/v1/heartbeat", credential=credential)
        return self.status(credential)

    def rotate(self, credential: str) -> str:
        value = self._request("POST", "/api/device/v1/rotate", credential=credential)
        new_token = value.get("credential")
        if not isinstance(new_token, str) or not new_token.startswith("mfd1."):
            raise DeviceClientError("The workspace returned an invalid replacement credential.")
        return new_token

    def disconnect(self, credential: str) -> None:
        self._request("POST", "/api/device/v1/disconnect", credential=credential)

    def activity(self, credential: str, events: list[dict]) -> int:
        value = self._request("POST", "/api/device/v1/activity", credential=credential, payload={"events": events})
        stored = value.get("stored")
        if not isinstance(stored, int) or stored < 0:
            raise DeviceClientError("The workspace did not confirm this activity.")
        return stored

    def claim_work(self, credential: str) -> dict | None:
        """Poll only our pinned MeasuredField origin with this device's bearer."""
        value = self._request("POST", "/api/device/v1/work/claim", credential=credential)
        job = value.get("job")
        if job is None:
            return None
        if (not isinstance(job, dict) or not isinstance(job.get("job_id"), str)
                or job.get("action") not in ("write_file", "make_directory")
                or not isinstance(job.get("payload"), dict)
                or not isinstance(job.get("lease_token"), str)):
            raise DeviceClientError("Invalid work response; no file change was made.")
        return job

    def finish_work(self, credential: str, job_id: str, lease: str,
                    *, succeeded: bool, digest: str | None = None,
                    error_code: str | None = None) -> dict:
        """Report actual result; never send local pairing token to the server."""
        result = self._request(
            "POST", "/api/device/v1/work/" + job_id + "/finish",
            credential=credential, payload={
                "lease_token": lease, "succeeded": succeeded,
                "result_sha256": digest, "error_code": error_code,
            })
        if result.get("job_id") != job_id or result.get("state") not in ("succeeded", "failed"):
            raise DeviceClientError("Workspace did not confirm the work receipt.")
        return result
