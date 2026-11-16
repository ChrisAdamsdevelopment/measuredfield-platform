"""MF-034: account computer client and fail-closed local permissions.

Only fake devices/businesses. A single integration test exercises the real MF-032
HTTP routes against disposable PostgreSQL when the full audit runner is used.
"""
from __future__ import annotations

import json
import sys

import pytest

from app.account_link.client import (DeviceApi, DeviceClientError, DeviceRevoked,
                                     DeviceStatus, PairingIntro, validate_origin)
from app.account_link.controller import AccountLinkController, AccountBoundPairingController
from app.account_link.secret_store import SecureStoreUnavailable, WindowsCredentialStore
from app.local_connect import AccessDenied, LocalConnect
from app.paired_connect.registry import PairingRegistry


class MemoryCredentialStore:
    def __init__(self):
        self.secret = None
        self.clears = 0

    def save(self, credential):
        self.secret = credential

    def load(self):
        return self.secret

    def clear(self):
        self.clears += 1
        self.secret = None


class FakeDeviceApi:
    def __init__(self, origin="https://workspace.example"):
        self.origin = validate_origin(origin)
        self.mode = "pending"
        self.access = "read"
        self.closed = 0
        self.disconnected = False
        self.credential = "mfd1.synthetic-org." + "X" * 40

    def pair(self, code, device_name):
        assert code == "ABCD-EFGH"
        return PairingIntro("faked-device-id", self.credential, "314159", "tomorrow")

    def status(self, token):
        assert token == self.credential
        if self.mode == "revoked":
            raise DeviceRevoked("Disconnected")
        if self.mode == "offline":
            raise DeviceClientError("Offline")
        return DeviceStatus("faked-device-id", self.mode, self.access, "Sample Business", "My computer")

    def disconnect(self, token):
        assert token == self.credential
        self.disconnected = True

    def close(self):
        self.closed += 1


def setup(tmp_path):
    local = LocalConnect(tmp_path / "local")
    registry = PairingRegistry(local.home)
    fake = FakeDeviceApi()
    creds = MemoryCredentialStore()
    controller = AccountLinkController(local, credential_store=creds,
                                       client_factory=lambda _: fake, registry=registry)
    return local, registry, fake, creds, controller


def test_url_validation_https_and_explicit_loopback_only():
    assert validate_origin("https://business.example") == "https://business.example"
    assert validate_origin("http://localhost:8000") == "http://localhost:8000"
    assert validate_origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert validate_origin("http://[::1]:8000") == "http://[::1]:8000"
    for bad in ("http://business.example", "http://192.168.1.1:9000",
                "http://10.0.0.1", "https://user:pw@business.example",
                "https://business.example/login", "https://business.example/?token=x",
                "https://business.example/#device", "file:///etc/passwd",
                "javascript:alert(1)", "", "https://business.example:bad"):
        with pytest.raises(DeviceClientError):
            validate_origin(bad)


def test_pending_code_only_in_memory_and_state_never_persists_credential(tmp_path):
    local, registry, api, creds, controller = setup(tmp_path)
    state = controller.connect(api.origin, "ABCD-EFGH", "Office PC")
    assert state.status == "pending" and state.check_number == "314159"
    assert creds.load() == api.credential
    raw = controller.path.read_text(encoding="utf-8")
    assert all(term not in raw for term in (api.credential, "ABCD-EFGH", "314159"))
    assert json.loads(raw)["status"] == "pending"
    api.mode = "active"
    active = controller.refresh()
    assert active.status == "active" and active.business == "Sample Business"
    assert active.check_number == ""
    assert not controller.can_suggest_edits()
    assert api.closed >= 2


def test_read_only_disallows_suggestions_and_revokes_earlier_write_sessions(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "example_project"
    folder.mkdir()
    grant = local.grant("Example", str(folder), "readwrite")
    local_pair = registry.create("Old proposed edits", [grant["id"]],
                                 allow_proposals=True, owner_confirmed=True)
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    # Pending state already shuts down an old suggested-change pairing.
    assert next(row for row in registry.list() if row["pairing_id"] == local_pair["pairing_id"])["status"] == "revoked"
    api.mode, api.access = "active", "read"
    account.refresh()
    pairing = AccountBoundPairingController(account)
    with pytest.raises(AccessDenied, match="Read only"):
        pairing.pair("Unapproved", [grant["id"]], confirmed=True,
                     allow_suggestions=True, confirmed_suggestions=True)
    safe = pairing.pair("Read only", [grant["id"]], confirmed=True)
    assert safe["read_only"] is True


def test_remote_disconnect_clears_credential_and_revokes_every_local_pairing(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "example_project"
    folder.mkdir()
    grant = local.grant("Example", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode, api.access = "active", "suggest"
    account.refresh()
    registry.create("Trusted pairing", [grant["id"]],
                    allow_proposals=True, owner_confirmed=True)
    assert registry.list()[0]["status"] == "active"
    api.mode = "revoked"
    view = account.refresh()
    assert view.status == "revoked" and creds.load() is None and creds.clears == 1
    assert registry.list()[0]["status"] == "revoked"
    assert account.can_suggest_edits() is False
    assert api.credential not in account.path.read_text(encoding="utf-8")


def test_network_unavailable_fails_closed_then_recovers(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "example_project"
    folder.mkdir()
    grant = local.grant("Example", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode, api.access = "active", "suggest"
    account.refresh()
    registry.create("Trusted pairing", [grant["id"]], allow_proposals=True, owner_confirmed=True)
    api.mode = "offline"
    state = account.refresh()
    assert state.status == "unverified" and registry.list()[0]["status"] == "revoked"
    assert not account.can_suggest_edits()
    api.mode = "active"
    assert account.refresh().status == "active"


def test_local_disconnect_never_depends_on_network_and_clears_local_tokens(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode = "active"
    account.refresh()
    status = account.disconnect()
    assert status.status == "revoked" and creds.load() is None
    assert api.disconnected
    assert account.view().check_number == ""


def test_windows_dpapi_protected_file_or_fail_closed_on_other_platforms(tmp_path):
    secret = "mfd1." + "a" * 70
    store = WindowsCredentialStore(tmp_path / "secrets")
    if sys.platform == "win32":
        store.save(secret)
        data = store.file.read_bytes()
        assert secret.encode() not in data and len(data) > len(secret)
        assert store.load() == secret
        store.clear()
        assert store.load() is None
    else:
        with pytest.raises(SecureStoreUnavailable):
            store.save(secret)
        assert not store.file.exists()


class FailingResponse:
    status_code = 500
    text = "LEAKSECRET:" + "X" * 40


class FailingClient:
    def get(self, path, **kwargs):
        return FailingResponse()


def test_error_does_not_expose_remote_text_or_credential():
    api = DeviceApi("http://localhost", client=FailingClient())
    secret = "mfd1." + "X" * 40
    with pytest.raises(DeviceClientError) as failure:
        api.status(secret)
    assert secret not in str(failure.value) and "LEAKSECRET" not in str(failure.value)


def test_device_api_against_real_routes_and_disposable_postgresql(platform, tmp_path):
    from .conftest import client_for, csrf_of, onboard_business
    from .test_pg_devices import get_code
    owner, org, _ = onboard_business(platform, business="Example Link Studio",
                                     owner_email="owner@account-link-example.test")
    computer = client_for(platform)
    device = DeviceApi("http://localhost", client=computer)
    local = LocalConnect(tmp_path / "computer")
    creds = MemoryCredentialStore()  # API integration independent of OS DPAPI implementation.
    controller = AccountLinkController(local, credential_store=creds,
                                       client_factory=lambda _: device)
    code = get_code(owner, "Example desktop", access="read")
    pending = controller.connect("http://localhost", code, "Example desktop")
    assert pending.status == "pending" and pending.check_number.isdigit()
    assert controller.refresh().status == "pending"
    assert owner.post(f"/app/computers/{pending.device_id}/confirm",
                      data={"csrf": csrf_of(owner), "check_number": pending.check_number}).status_code == 303
    connected = controller.refresh()
    assert connected.status == "active" and connected.access == "read"
    assert connected.business == "Example Link Studio"
    owner.post(f"/app/computers/{pending.device_id}/disconnect", data={"csrf": csrf_of(owner)})
    revoked = controller.refresh()
    assert revoked.status == "revoked" and creds.load() is None
