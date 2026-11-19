"""Real disk outcomes from synthetic cloud jobs, with all owner gates enforced."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from app.account_link.runtime_guard import AccountRuntimeGuard
from app.desktop_companion.work_poller import WorkPoller
from app.local_connect.store import LocalConnect
from app.paired_connect.registry import PairingRegistry


def _owner_env(tmp_path, monkeypatch):
    home = tmp_path / "private"
    root = tmp_path / "owner-project"
    root.mkdir()
    local = LocalConnect(home)
    grant = local.grant("Owner website", root, "readwrite")["id"]
    registry = PairingRegistry(home)
    pair = registry.create(
        "Temporary approved session", [grant], owner_confirmed=True,
        allow_writes=True, owner_confirmed_write=True, expires_minutes=20)
    monkeypatch.setattr(AccountRuntimeGuard, "check", lambda self, *, action: None)
    class Account:
        secrets = SimpleNamespace(load=lambda: "synthetic-device-credential")
        def refresh(self):
            return SimpleNamespace(status="active", access="work",
                                   server_url="https://trusted.measuredfield.test")
    return root, grant, registry, pair["pairing_id"], Account()


def _job(grant, *, content="Hello from queued ChatGPT work"):
    return {
        "job_id": "00000000-0000-0000-0000-000000000099",
        "lease_token": "lease-value-not-a-real-credential",
        "action": "write_file",
        "payload": {
            "project_id": grant, "relative_path": "index.html",
            "content": content, "expected_sha256": None,
        },
    }


def test_poller_saves_actual_file_and_reports_digest(tmp_path, monkeypatch):
    root, grant, registry, pair_id, account = _owner_env(tmp_path, monkeypatch)
    job = _job(grant)
    receipts = []
    class FakeApi:
        def claim_work(self, credential):
            assert credential == "synthetic-device-credential"
            return job
        def finish_work(self, credential, job_id, lease, **kwargs):
            receipts.append(kwargs)
            return {"job_id": job_id, "state": "succeeded" if kwargs["succeeded"] else "failed"}
        def close(self): pass
    poller = WorkPoller(
        pairing_id=pair_id, registry=registry, account=account,
        api_factory=lambda origin: FakeApi())
    assert poller.once() is True
    target = root / "index.html"
    assert target.read_text() == job["payload"]["content"]
    assert receipts == [{"succeeded": True, "digest": hashlib.sha256(target.read_bytes()).hexdigest()}]
    # Retried same job does not write or replace a same-content file.
    assert poller.once() is True and target.read_text() == job["payload"]["content"]


def test_revoked_local_pairing_prevents_remote_file_write(tmp_path, monkeypatch):
    root, grant, registry, pair_id, account = _owner_env(tmp_path, monkeypatch)
    registry.revoke(pair_id)
    notices = []
    class FakeApi:
        def claim_work(self, credential): return _job(grant)
        def finish_work(self, credential, job_id, lease, **kwargs):
            notices.append(kwargs)
        def close(self): pass
    poller = WorkPoller(
        pairing_id=pair_id, registry=registry, account=account,
        api_factory=lambda origin: FakeApi())
    from app.local_connect.store import AccessDenied
    import pytest
    with pytest.raises(AccessDenied):
        poller.once()  # revoked owner pairing is rejected BEFORE cloud polling
    assert not (root / "index.html").exists()
    assert notices == []


def test_poller_does_not_claim_without_verified_work_account(tmp_path, monkeypatch):
    root, grant, registry, pair_id, account = _owner_env(tmp_path, monkeypatch)
    account.refresh = lambda: SimpleNamespace(status="active", access="suggest",
                                             server_url="https://trusted.measuredfield.test")
    called = []
    poller = WorkPoller(
        pairing_id=pair_id, registry=registry, account=account,
        api_factory=lambda origin: called.append(origin))
    from app.local_connect.store import AccessDenied
    import pytest
    with pytest.raises(AccessDenied):
        poller.once()
    assert called == [] and not (root / "index.html").exists()


def test_default_worker_does_not_revoke_fresh_owner_pairing(tmp_path, monkeypatch):
    """Headless worker must not call account UI startup that revokes all sessions."""
    root, grant, registry, pair_id, account = _owner_env(tmp_path, monkeypatch)
    import app.desktop_companion.work_poller as module

    class WorkerGuard:
        def __init__(self, home):
            assert home == registry.home
            self.secrets = SimpleNamespace(load=lambda: "synthetic-device-credential")
        def check(self, *, action):
            assert action == "write"
        def _read_account(self):
            return {
                "status": "active", "access": "work",
                "device_id": "approved-test-device",
                "server_url": "https://trusted.measuredfield.test",
            }

    class NoWorkApi:
        def claim_work(self, credential):
            assert credential == "synthetic-device-credential"
            return None
        def close(self): pass

    monkeypatch.setattr(module, "AccountRuntimeGuard", WorkerGuard)
    poller = module.WorkPoller(
        pairing_id=pair_id, registry=registry,
        api_factory=lambda origin: NoWorkApi())
    assert registry.authorize(
        pair_id, registry.token_for_local_launcher(pair_id))["can_write"] is True
    assert poller.once() is False
    assert registry.authorize(
        pair_id, registry.token_for_local_launcher(pair_id))["can_write"] is True
    registry.revoke(pair_id)
    from app.local_connect.store import AccessDenied
    import pytest
    with pytest.raises(AccessDenied):
        poller.once()


def test_worker_main_handles_transient_cloud_failure_without_crashing(monkeypatch, capsys):
    import sys
    import app.desktop_companion.work_poller as module
    from app.account_link.client import DeviceClientError, DeviceRevoked
    calls = []
    class TemporaryFailure:
        def __init__(self, **kwargs): pass
        def once(self):
            calls.append("poll")
            if len(calls) == 1:
                raise DeviceClientError("Synthetic network interruption")
            raise KeyboardInterrupt()
    monkeypatch.setattr(module, "WorkPoller", TemporaryFailure)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["work_poller", "--pairing-id", "synthetic-pair-id"])
    assert module.main() == 0
    assert calls == ["poll", "poll"]
    assert "No work completion is claimed" in capsys.readouterr().out

    class Revoked:
        def __init__(self, **kwargs): pass
        def once(self): raise DeviceRevoked("Synthetic revoked bearer")
    monkeypatch.setattr(module, "WorkPoller", Revoked)
    assert module.main() == 2
    assert "disconnected" in capsys.readouterr().out


def test_idempotent_existing_directory_is_safe(tmp_path, monkeypatch):
    root, grant, registry, pair_id, account = _owner_env(tmp_path, monkeypatch)
    from app.paired_connect.registry import BoundSession
    session = BoundSession(
        pair_id, registry.token_for_local_launcher(pair_id), registry.home)
    first = session.make_directory(grant, "assets")
    second = session.make_directory(grant, "assets")
    assert first["created"] is True
    assert second["created"] is False
    assert (root / "assets").is_dir()
