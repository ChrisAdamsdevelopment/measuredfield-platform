"""MF-043: synthetic cross-process-equivalent account revocation for stdio MCP.

Every scenario uses a disposable local folder and fake provider client. No real
device, credentials, production account, provider or network calls are used.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.account_link.client import DeviceClientError, DeviceRevoked, DeviceStatus
from app.local_connect import AccessDenied
from app.paired_connect.registry import PairingRegistry, BoundSession
from app.account_link import runtime_guard


class FakeDevice:
    def __init__(self, origin="https://workspace.example"):
        self.origin = origin
        self.status_mode = "active"
        self.access = "suggest"
        self.id = "device-one"
        self.calls = 0
        self.closed = 0
        self.on_check = None

    def status(self, credential):
        assert credential == "mfd1.fake-runtime-" + "Q" * 40
        self.calls += 1
        if self.on_check is not None:
            self.on_check()
        if self.status_mode == "revoked":
            raise DeviceRevoked("Disconnected by owner")
        if self.status_mode == "offline":
            raise DeviceClientError("Simulated network outage")
        return DeviceStatus(
            device_id=self.id, status=self.status_mode, access=self.access,
            business="Synthetic business", name="Synthetic computer")

    def close(self):
        self.closed += 1


@pytest.fixture
def site(tmp_path, monkeypatch):
    registry = PairingRegistry(tmp_path / "state")
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("safe local demo", encoding="utf-8")
    grant = registry.connect.grant("Demo", project, "readwrite")["id"]
    pair_a = registry.create("One", [grant], owner_confirmed=True,
                             allow_proposals=True)["pairing_id"]
    pair_b = registry.create("Two", [grant], owner_confirmed=True,
                             allow_proposals=True)["pairing_id"]
    token_a = registry.token_for_local_launcher(pair_a)
    fake = FakeDevice()
    monkeypatch.setenv("MEASUREDFIELD_WORKSPACE_URL", fake.origin)
    monkeypatch.delenv("MEASUREDFIELD_DEVELOPER_MODE", raising=False)
    # Exercise the real process-independent state and SQLite pairing records,
    # but with synthetic bearer and provider status instead of outbound HTTPS.
    monkeypatch.setattr(runtime_guard.WindowsCredentialStore, "load",
                        lambda _: "mfd1.fake-runtime-" + "Q" * 40)
    monkeypatch.setattr(runtime_guard, "DeviceApi", lambda origin: (
        fake if origin == fake.origin else (_ for _ in ()).throw(AssertionError(
            "Unexpected untrusted outbound address"))))
    return {
        "registry": registry, "grant": grant, "pair_a": pair_a,
        "pair_b": pair_b, "token_a": token_a, "fake": fake,
        "state": registry.home / "account_connection.json",
        "credential_file": registry.home / "account_device.dpapi",
    }


def _state(site, **changes):
    record = {
        "status": "active", "server_url": site["fake"].origin,
        "device_id": site["fake"].id, "access": "suggest",
        "business": "Synthetic business", "name": "Synthetic computer",
    }
    record.update(changes)
    site["state"].write_text(json.dumps(record), encoding="utf-8")


def _open(site):
    # New PairingRegistry instance models a different stdio MCP server process.
    return BoundSession(site["pair_a"], site["token_a"], site["registry"].home)


def _assert_all_revoked(site):
    items = site["registry"].list()
    assert len(items) == 2
    assert all(item["status"] == "revoked" for item in items)


def test_never_linked_legacy_local_owner_pilot_is_still_scoped(site):
    server = _open(site)
    assert server.read(site["grant"], "note.txt")["content"] == "safe local demo"
    assert server.projects()[0]["id"] == site["grant"]
    assert site["fake"].calls == 0  # no cloud calls when never linked


def test_live_linked_device_checked_at_every_tool_call_when_ui_closed(site):
    _state(site)
    server = _open(site)
    assert server.read(site["grant"], "note.txt")["content"] == "safe local demo"
    assert server.projects()
    assert server.browse(site["grant"])
    proposal = server.propose(site["grant"], "note.txt", "Proposed safe edit")
    assert proposal["owner_approval_required"] is True
    assert site["fake"].calls == 4
    assert site["fake"].closed == 4


def test_disconnect_in_workspace_stops_already_running_server_on_next_action(site):
    _state(site)
    server = _open(site)
    assert server.read(site["grant"], "note.txt")
    assert site["registry"].list()[0]["status"] == "active"
    site["fake"].status_mode = "revoked"
    with pytest.raises(AccessDenied, match="not verified"):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)
    old_calls = site["fake"].calls
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    assert site["fake"].calls == old_calls  # local revoke is permanent


def test_offline_does_not_extend_cached_permissions(site):
    _state(site)
    server = _open(site)
    server.read(site["grant"], "note.txt")
    site["fake"].status_mode = "offline"
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_cloud_read_only_revokes_old_write_pairing(site):
    _state(site)
    server = _open(site)
    server.read(site["grant"], "note.txt")
    site["fake"].access = "read"
    with pytest.raises(AccessDenied):
        server.propose(site["grant"], "note.txt", "Should never be staged")
    _assert_all_revoked(site)
    assert site["registry"].connect.pending() == []


@pytest.mark.parametrize("bad", [
    {"status": "pending"},
    {"status": "revoked"},
    {"status": "unverified"},
    {"server_url": ""},
    {"server_url": "https://measuredfie1d-login.example"},
    {"device_id": "someone-elses-device"},
])
def test_linked_account_mutations_fail_closed_without_leaking_files(site, bad):
    _state(site, **bad)
    server = _open(site)
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_server_status_device_id_mismatch_is_denied(site):
    _state(site)
    server = _open(site)
    site["fake"].id = "attacker-mismatched-device"
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_credential_file_without_account_state_not_treated_as_never_linked(site):
    site["credential_file"].write_bytes(b"synthetic-encrypted-blob")
    server = _open(site)
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_saved_state_downgraded_to_not_connected_cannot_revive_old_pairing(site):
    _state(site, status="not_connected")
    server = _open(site)
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_invalid_json_or_linked_record_without_credential_denies(site, monkeypatch):
    site["state"].write_text("{broken", encoding="utf-8")
    server = _open(site)
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_stale_saved_state_during_network_check_is_refused(site):
    _state(site)
    server = _open(site)
    site["fake"].on_check = lambda: _state(site, status="revoked")
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_unavailable_workspace_pin_revokes_existing_pairings(site, monkeypatch):
    _state(site)
    server = _open(site)
    monkeypatch.delenv("MEASUREDFIELD_WORKSPACE_URL")
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_linked_account_missing_protected_bearer_is_denied(site, monkeypatch):
    _state(site)
    server = _open(site)
    monkeypatch.setattr(runtime_guard.WindowsCredentialStore, "load", lambda _: None)
    with pytest.raises(AccessDenied):
        server.read(site["grant"], "note.txt")
    _assert_all_revoked(site)


def test_running_paired_process_refuses_after_owner_disconnect(site):
    """Real separate Python process loses its paired file grant after link state changes.

    The process starts with a never-linked local-only pilot, loads its pairing
    token from local protected storage (never argv/env), then keeps its BoundSession
    alive while the parent writes a synthetic revoked account record. No network
    service or actual customer file is involved.
    """
    child = """
import os
import sys

from app.paired_connect.registry import BoundSession, PairingRegistry

registry = PairingRegistry(os.environ["MF_TEST_HOME"])
pair_id = os.environ["MF_TEST_PAIR"]
token = registry.token_for_local_launcher(pair_id)
session = BoundSession(pair_id, token, registry.home)
for command in sys.stdin:
    if command.strip() == "stop":
        break
    try:
        data = session.read(os.environ["MF_TEST_GRANT"], "note.txt")
        print("OK:" + data["content"], flush=True)
    except Exception as exc:
        print("DENIED:" + type(exc).__name__, flush=True)
"""
    env = {
        **os.environ,
        "MF_TEST_HOME": str(site["registry"].home),
        "MF_TEST_PAIR": site["pair_a"],
        "MF_TEST_GRANT": site["grant"],
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    process = subprocess.Popen(
        [sys.executable, "-B", "-u", "-c", child],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write("read\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "OK:safe local demo"

        _state(site, status="revoked")
        process.stdin.write("read\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "DENIED:AccessDenied"
        _assert_all_revoked(site)

        process.stdin.write("stop\n")
        process.stdin.flush()
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
