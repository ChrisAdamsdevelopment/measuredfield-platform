"""MF-034: account computer client and fail-closed local permissions.

Only fake devices/businesses. A single integration test exercises the real MF-032
HTTP routes against disposable PostgreSQL when the full audit runner is used.
"""
from __future__ import annotations

import json
import sys

import pytest

from app.account_link.client import (
    DeviceApi, DeviceClientError, DeviceRevoked,
    DeviceStatus, PairingIntro, validate_origin, resolve_trusted_origin,
)
from app.account_link.controller import AccountLinkController, AccountBoundPairingController
from app.account_link.secret_store import SecureStoreUnavailable, WindowsCredentialStore
from app.local_connect import AccessDenied, LocalConnect
from app.paired_connect.registry import PairingRegistry


@pytest.fixture(autouse=True)
def trusted_workspace_for_tests(monkeypatch):
    # A first-party installation pins its origin. Tests never use a real service.
    monkeypatch.setenv("MEASUREDFIELD_WORKSPACE_URL", "https://workspace.example")
    monkeypatch.delenv("MEASUREDFIELD_DEVELOPER_MODE", raising=False)


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


def test_error_does_not_expose_remote_text_or_credential(monkeypatch):
    monkeypatch.delenv("MEASUREDFIELD_WORKSPACE_URL")
    monkeypatch.setenv("MEASUREDFIELD_DEVELOPER_MODE", "1")
    api = DeviceApi("http://localhost", client=FailingClient())
    secret = "mfd1." + "X" * 40
    with pytest.raises(DeviceClientError) as failure:
        api.status(secret)
    assert secret not in str(failure.value) and "LEAKSECRET" not in str(failure.value)


def test_device_api_against_real_routes_and_disposable_postgresql(platform, tmp_path, monkeypatch):
    monkeypatch.delenv("MEASUREDFIELD_WORKSPACE_URL")
    monkeypatch.setenv("MEASUREDFIELD_DEVELOPER_MODE", "1")
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



# MF-034 independent Argon security-review regressions (2026-11-16).

def test_undecryptable_device_credential_cannot_keep_active_suggestion_access(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode, api.access = "active", "suggest"
    account.refresh()
    registry.create("Old session", [grant["id"]], owner_confirmed=True, allow_proposals=True)
    # Simulate CryptUnprotectData failure or a corrupted DPAPI file.
    def corrupted_credential():
        raise SecureStoreUnavailable("cannot decrypt")
    creds.load = corrupted_credential
    state = account.refresh()
    assert state.status == "revoked" and state.access == ""
    assert not account.can_suggest_edits()
    assert all(row["status"] == "revoked" for row in registry.list())


def test_failed_credential_deletion_cannot_preserve_active_state(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode, api.access = "active", "suggest"
    account.refresh()
    registry.create("Old session", [grant["id"]], owner_confirmed=True, allow_proposals=True)
    def blocked_clear():
        raise OSError("locked DPAPI file")
    creds.clear = blocked_clear
    api.mode = "revoked"
    state = account.refresh()
    assert state.status == "revoked" and state.access == ""
    assert not account.can_suggest_edits()
    assert all(row["status"] == "revoked" for row in registry.list())
    assert "cleanup requires attention" in state.notice


@pytest.mark.parametrize("invalid_state", ['{"status":', '{"status":"totally_unknown"}',
                                          "null"])
def test_corrupted_state_files_fail_closed_even_when_credential_exists(tmp_path, invalid_state):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode, api.access = "active", "suggest"
    account.refresh()
    registry.create("Old session", [grant["id"]], owner_confirmed=True, allow_proposals=True)
    account.path.write_text(invalid_state, encoding="utf-8")
    restarted = AccountLinkController(local, credential_store=creds,
                                      client_factory=lambda _: api, registry=registry)
    assert restarted.view().status == "revoked"
    assert not restarted.can_suggest_edits()
    assert all(row["status"] == "revoked" for row in registry.list())


def test_missing_state_with_existing_credential_fails_closed(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    account.path.unlink()
    restarted = AccountLinkController(local, credential_store=creds,
                                      client_factory=lambda _: api, registry=registry)
    assert restarted.view().status == "revoked"
    assert not restarted.can_suggest_edits()


def test_cached_active_suggest_permission_not_trusted_after_window_reopens(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode, api.access = "active", "suggest"
    account.refresh()
    registry.create("Old session", [grant["id"]], owner_confirmed=True, allow_proposals=True)
    reopened = AccountLinkController(local, credential_store=creds,
                                     client_factory=lambda _: api, registry=registry)
    assert reopened.view().status == "unverified"
    assert not reopened.can_suggest_edits()
    assert registry.list()[0]["status"] == "revoked"
    # Only an authenticated new status request may re-establish Suggest permission.
    assert reopened.refresh().status == "active"
    assert reopened.can_suggest_edits()


@pytest.mark.parametrize("mode", ["pending", "unverified", "revoked"])
def test_no_new_read_only_local_pairing_while_unconfirmed_or_revoked(tmp_path, mode):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    account._state["status"] = mode
    pairing = AccountBoundPairingController(account)
    with pytest.raises(AccessDenied, match="must be confirmed"):
        pairing.pair("Forbidden", [grant["id"]], confirmed=True)
    assert not any(row["status"] == "active" for row in registry.list())


def test_pairing_refresh_checks_remote_revocation_at_action_time(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.mode, api.access = "active", "suggest"
    account.refresh()
    api.mode = "revoked"
    with pytest.raises(AccessDenied, match="must be confirmed"):
        AccountBoundPairingController(account).pair(
            "Cannot reconnect", [grant["id"]], confirmed=True,
            allow_suggestions=True, confirmed_suggestions=True)
    assert account.view().status == "revoked"


def test_partial_connect_state_save_failure_rolls_back_and_revokes(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    registry.create("Old pairing", [grant["id"]], owner_confirmed=True,
                    allow_proposals=True)
    def fail_save():
        raise OSError("state file unavailable")
    account._save = fail_save
    with pytest.raises(AccessDenied, match="could not be completed"):
        account.connect(api.origin, "ABCD-EFGH", "Office PC")
    assert api.disconnected is True
    assert creds.load() is None
    assert account.view().status == "unverified"
    assert not account.can_suggest_edits()
    assert all(row["status"] == "revoked" for row in registry.list())


def test_device_identity_mismatch_revokes_all_trusted_local_sessions(tmp_path):
    local, registry, api, creds, account = setup(tmp_path)
    account.connect(api.origin, "ABCD-EFGH", "Office PC")
    api.status = lambda token: DeviceStatus("unknown-device", "active", "suggest",
                                            "Sample Business", "Office PC")
    assert account.refresh().status == "revoked"
    assert creds.load() is None


def test_redirect_is_rejected_without_forwarding_bearer_token(monkeypatch):
    monkeypatch.delenv("MEASUREDFIELD_WORKSPACE_URL")
    monkeypatch.setenv("MEASUREDFIELD_DEVELOPER_MODE", "1")
    class RedirectingClient:
        def get(self, path, **kwargs):
            class Response:
                status_code = 302
                headers = {"Location": "https://unknown.example/collect"}
                text = "private response body"
            return Response()
    client = DeviceApi("http://localhost", client=RedirectingClient())
    with pytest.raises(DeviceClientError, match="redirected") as failure:
        client.status("mfd1." + "X" * 40)
    assert "private response" not in str(failure.value)
    assert "unknown.example" not in str(failure.value)


# MF-034 second independent Claude review: pin real identity before sharing a pairing code.
def test_pairing_intro_never_renders_device_secret_in_debugging():
    secret = "mfd1." + "Q" * 50
    intro = PairingIntro("test-device", secret, "314159", "later")
    assert secret not in repr(intro)
    assert secret not in str(intro)


def test_pinned_workspace_rejects_lookalike_before_network(tmp_path):
    local, registry, fake, creds, _ = setup(tmp_path)
    opened = []
    account = AccountLinkController(
        local, credential_store=creds, registry=registry,
        client_factory=lambda url: (opened.append(url), fake)[1],
    )
    for lookalike in ("https://measuredfie1d-login.example",
                      "https://workspace.example.evil.test",
                      "https://workspace.example:443",
                      "http://localhost:8000",
                      "https://user@workspace.example"):
        with pytest.raises(DeviceClientError):
            account.connect(lookalike, "ABCD-EFGH", "Desktop")
    assert opened == []
    assert creds.load() is None
    assert account.view().status == "not_connected"
    assert resolve_trusted_origin("") == "https://workspace.example"
    # Even an injected HTTP client cannot skip the exact origin check.
    with pytest.raises(DeviceClientError):
        DeviceApi("https://workspace.example.evil.test", client=object())


def test_unconfigured_owner_installation_refuses_any_network(monkeypatch, tmp_path):
    monkeypatch.delenv("MEASUREDFIELD_WORKSPACE_URL")
    monkeypatch.delenv("MEASUREDFIELD_DEVELOPER_MODE", raising=False)
    with pytest.raises(DeviceClientError, match="has not been installed"):
        resolve_trusted_origin("https://workspace.example")
    local, registry, fake, creds, _ = setup(tmp_path)
    calls = []
    account = AccountLinkController(local, credential_store=creds, registry=registry,
                                    client_factory=lambda url: (calls.append(url), fake)[1])
    with pytest.raises(DeviceClientError, match="has not been installed"):
        account.connect("https://workspace.example", "ABCD-EFGH", "Desktop")
    assert calls == []


def test_developer_mode_allows_only_same_computer(monkeypatch):
    monkeypatch.delenv("MEASUREDFIELD_WORKSPACE_URL")
    monkeypatch.setenv("MEASUREDFIELD_DEVELOPER_MODE", "1")
    assert resolve_trusted_origin("http://127.0.0.1:7000") == "http://127.0.0.1:7000"
    assert resolve_trusted_origin("http://[::1]:7000") == "http://[::1]:7000"
    assert resolve_trusted_origin("http://localhost:7000") == "http://localhost:7000"
    for hostile in ("https://measuredfie1d-login.example", "https://workspace.example",
                    "http://192.168.0.11:7000", "https://10.0.0.1"):
        with pytest.raises(DeviceClientError):
            resolve_trusted_origin(hostile)


def test_tampered_persisted_workspace_is_revoked_before_client_network(tmp_path):
    local, registry, fake, creds, account = setup(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    grant = local.grant("Project", str(folder), "readwrite")
    account.connect(fake.origin, "ABCD-EFGH", "Desktop")
    fake.mode, fake.access = "active", "suggest"
    account.refresh()
    registry.create("Prior session", [grant["id"]], owner_confirmed=True,
                    allow_proposals=True)
    cached = json.loads(account.path.read_text(encoding="utf-8"))
    cached["server_url"] = "https://measuredfie1d-login.example"
    account.path.write_text(json.dumps(cached), encoding="utf-8")
    opened = []
    restarted = AccountLinkController(
        local, registry=registry, credential_store=creds,
        client_factory=lambda url: (opened.append(url), fake)[1],
    )
    assert restarted.view().status == "revoked"
    assert restarted.view().access == ""
    assert "not trusted" in restarted.view().notice
    assert all(r["status"] == "revoked" for r in registry.list())
    assert creds.load() is None
    assert opened == []


def test_damaged_missing_saved_origin_never_causes_uncaught_refresh(tmp_path):
    local, registry, fake, creds, account = setup(tmp_path)
    account.connect(fake.origin, "ABCD-EFGH", "Desktop")
    account._state["server_url"] = ""
    # Explicitly succeeds with a safe return instead of exposing a RuntimeError
    # to the Tk callback or sending the code/bearer to a guessable server.
    view = account.refresh()
    assert view.status == "revoked"
    assert view.access == ""
    assert account.can_suggest_edits() is False
    assert creds.load() is None


def test_wrong_pinned_workspace_configuration_fails_closed(monkeypatch):
    for invalid in ("http://real-workspace.example",
                    "http://localhost:5000",
                    "https://localhost:5000",
                    "https://user:password@real-workspace.example"):
        monkeypatch.setenv("MEASUREDFIELD_WORKSPACE_URL", invalid)
        with pytest.raises(DeviceClientError):
            resolve_trusted_origin("https://workspace.example")
