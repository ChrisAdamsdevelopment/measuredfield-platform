"""MF-024 production-path local Work mode: real files, permissions, revocation.

All tests use synthetic device status and temporary project roots. No live
ChatGPT, cloud connection, account credential or customer file is touched.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.account_link.runtime_guard import AccountRuntimeGuard
from app.desktop_companion.work_actions import WorkActions
from app.local_connect.store import AccessDenied, ConflictError, LocalConnect
from app.paired_connect.registry import BoundSession, PairingRegistry


def _owner_folder(tmp_path):
    root = tmp_path / "approved-project"
    root.mkdir()
    local = LocalConnect(tmp_path / "private")
    grant = local.grant("Client Work", root, "readwrite")
    registry = PairingRegistry(local.home)
    return root, local, grant["id"], registry


def _session(registry, grant_id, *, allow_writes=True, approve=True):
    return registry.create(
        "Work session", [grant_id], owner_confirmed=True,
        allow_writes=allow_writes, owner_confirmed_write=approve,
        expires_minutes=20,
    )


def test_direct_work_creates_real_file_and_updates_with_baseline(tmp_path, monkeypatch):
    root, local, grant, registry = _owner_folder(tmp_path)
    actions = []
    monkeypatch.setattr(AccountRuntimeGuard, "check", lambda self, *, action: actions.append(action))
    session = _session(registry, grant)
    bound = BoundSession(session["pairing_id"], registry.token_for_local_launcher(session["pairing_id"]), local.home)
    created = bound.write_file(grant, "index.html", "<h1>Hello real project</h1>")
    target = root / "index.html"
    assert target.read_text() == "<h1>Hello real project</h1>"
    assert created["created"] and created["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    revised = bound.write_file(grant, "index.html", "<h1>Revised</h1>", created["sha256"])
    assert revised["created"] is False and target.read_text() == "<h1>Revised</h1>"
    assert actions.count("write") >= 6
    assert "work_file_write" in {e["action"] for e in local.audit()}
    assert registry.revoke(session["pairing_id"])
    with pytest.raises(AccessDenied):
        bound.write_file(grant, "after-revoke.txt", "Not authorized")
    assert not (root / "after-revoke.txt").exists()


def test_work_directory_creation_and_nested_file(tmp_path, monkeypatch):
    root, local, grant, registry = _owner_folder(tmp_path)
    monkeypatch.setattr(AccountRuntimeGuard, "check", lambda self, *, action: None)
    s = _session(registry, grant)
    bound = BoundSession(s["pairing_id"], registry.token_for_local_launcher(s["pairing_id"]), local.home)
    assert bound.make_directory(grant, "src")["created"]
    assert (root / "src").is_dir()
    assert bound.write_file(grant, "src/app.py", "print('hello')")["written"]
    with pytest.raises((AccessDenied, FileExistsError)):
        bound.make_directory(grant, "src")
    assert "work_directory_create" in {e["action"] for e in local.audit()}


def test_work_requires_explicit_owner_confirmation_even_with_readwrite_folder(tmp_path):
    root, local, grant, registry = _owner_folder(tmp_path)
    with pytest.raises(AccessDenied, match="explicitly approve"):
        _session(registry, grant, approve=False)
    assert registry.list() == []


def test_read_or_suggest_pairing_cannot_write(tmp_path, monkeypatch):
    root, local, grant, registry = _owner_folder(tmp_path)
    monkeypatch.setattr(AccountRuntimeGuard, "check", lambda self, *, action: None)
    s = _session(registry, grant, allow_writes=False)
    bound = BoundSession(s["pairing_id"], registry.token_for_local_launcher(s["pairing_id"]), local.home)
    with pytest.raises(AccessDenied, match="Work mode"):
        bound.write_file(grant, "deny.txt", "Cannot write")
    assert not (root / "deny.txt").exists()


def test_work_must_have_live_grant_and_reject_secret_paths(tmp_path, monkeypatch):
    root, local, grant, registry = _owner_folder(tmp_path)
    monkeypatch.setattr(AccountRuntimeGuard, "check", lambda self, *, action: None)
    s = _session(registry, grant)
    bound = BoundSession(s["pairing_id"], registry.token_for_local_launcher(s["pairing_id"]), local.home)
    for path in ("../escape.txt", "/absolute.txt", ".env", "secrets/private.txt",
                 "src/../../escape.txt", "C:/outside.txt"):
        with pytest.raises(AccessDenied):
            bound.write_file(grant, path, "Forbidden")
    assert not (tmp_path / "escape.txt").exists()
    assert local.revoke(grant)
    with pytest.raises(AccessDenied):
        bound.write_file(grant, "stale.txt", "Denied")


def test_blind_overwrite_and_races_are_rejected(tmp_path):
    root, local, grant, registry = _owner_folder(tmp_path)
    file = root / "existing.txt"
    file.write_text("Original")
    actions = WorkActions(local, lambda grant_id: None)
    with pytest.raises(ConflictError):
        actions.write_file(grant, "existing.txt", "Blind")
    old = hashlib.sha256(file.read_bytes()).hexdigest()
    file.write_text("Unexpected")
    with pytest.raises(ConflictError):
        actions.write_file(grant, "existing.txt", "Wrong", old)
    assert file.read_text() == "Unexpected"
    with pytest.raises(ConflictError):
        actions.write_file(grant, "missing.txt", "Wrong", old)


def test_authorization_rechecked_and_revoke_before_write_blocks(tmp_path):
    root, local, grant, registry = _owner_folder(tmp_path)
    count = [0]
    def authorize(g):
        count[0] += 1
        if count[0] > 1:
            raise AccessDenied("Work authorization revoked")
    actions = WorkActions(local, authorize)
    with pytest.raises(AccessDenied, match="revoked"):
        actions.write_file(grant, "no.txt", "Denied")
    assert not (root / "no.txt").exists()


def test_previous_local_pairings_upgrade_without_gain_of_work_access(tmp_path):
    root, local, grant, registry = _owner_folder(tmp_path)
    # Exercise the unchanged old entry's new column rather than invent a token.
    pair = registry.create("Old suggest", [grant], owner_confirmed=True,
                           allow_proposals=True)
    with sqlite3.connect(registry.db_path) as db:
        db.execute("UPDATE pairings SET allow_writes=0 WHERE id=?", (pair["pairing_id"],))
    upgraded = PairingRegistry(local.home)
    assert upgraded.list()[0]["can_write"] is False
    token = upgraded.token_for_local_launcher(pair["pairing_id"])
    with pytest.raises(AccessDenied):
        upgraded.authorize(pair["pairing_id"], token, grant, "write")


def test_guard_does_not_upgrade_unlinked_or_suggest_device_to_work(tmp_path, monkeypatch):
    root, local, grant, registry = _owner_folder(tmp_path)
    guard = AccountRuntimeGuard(local.home, credential_store=SimpleNamespace(load=lambda: None),
                                registry=registry)
    with pytest.raises(AccessDenied, match="verified account-linked"):
        guard.check(action="write")
    state_file = local.home / "account_connection.json"
    state_file.write_text(json.dumps({
        "status": "active", "server_url": "https://example.invalid",
        "device_id": "device-one", "access": "work",
    }))
    class Store:
        def load(self): return "synthetic-device-token"
    class Api:
        def __init__(self, mode): self.mode = mode
        def status(self, credential):
            return SimpleNamespace(status="active", device_id="device-one", access=self.mode)
        def close(self): pass
    monkeypatch.setattr("app.account_link.runtime_guard.resolve_trusted_origin",
                        lambda url: "https://validated.example.test")
    for mode in ("read", "suggest"):
        g = AccountRuntimeGuard(local.home, credential_store=Store(),
                                client_factory=lambda origin: Api(mode), registry=registry)
        with pytest.raises(AccessDenied):
            g.check(action="write")
    g = AccountRuntimeGuard(local.home, credential_store=Store(),
                            client_factory=lambda origin: Api("work"), registry=registry)
    g.check(action="write")


def test_owner_account_gate_controls_ability_to_issue_work_pairing(tmp_path):
    """A local writable folder is not permission to elevate a linked account."""
    from app.account_link.controller import AccountBoundPairingController
    root, local, grant, registry = _owner_folder(tmp_path)

    class FakeAccount:
        def __init__(self, may_work):
            self.local, self.may_work = local, may_work

        def view(self):
            return SimpleNamespace(status="active")

        def refresh(self):
            return self.view()

        def can_suggest_edits(self):
            return True

        def can_work(self):
            return self.may_work

    refused = AccountBoundPairingController(FakeAccount(False))
    with pytest.raises(AccessDenied, match="owner-approved Work"):
        refused.pair(
            "Denied escalation", [grant], minutes=5, allow_writes=True,
            confirmed=True, confirmed_writes=True,
        )
    assert not registry.list()
    granted = AccountBoundPairingController(FakeAccount(True))
    approved = granted.pair(
        "Owner-approved Work", [grant], minutes=5, allow_writes=True,
        confirmed=True, confirmed_writes=True,
    )
    assert approved["can_write"] is True
    assert len(registry.list()) == 1


def test_overlong_or_unconfirmed_local_work_pairings_denied(tmp_path):
    root, local, grant, registry = _owner_folder(tmp_path)
    with pytest.raises(AccessDenied, match="eight hours"):
        registry.create(
            "Excessive work session", [grant], owner_confirmed=True,
            allow_writes=True, owner_confirmed_write=True, expires_minutes=1440,
        )
    with pytest.raises(AccessDenied, match="explicitly approve"):
        registry.create(
            "Unconfirmed", [grant], owner_confirmed=True,
            allow_writes=True, owner_confirmed_write=False, expires_minutes=5,
        )
    assert not registry.list()
