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
    assert poller.once() is True
    assert not (root / "index.html").exists()
    assert notices[0]["succeeded"] is False


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
