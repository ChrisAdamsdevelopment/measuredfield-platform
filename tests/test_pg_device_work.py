"""MF-024 cloud-to-local direct-write relay, disposable PostgreSQL only."""
from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.mcp_connector.gateway import ConnectorAccessDenied
from app.models import DeviceWorkRequest
from app.services import device_work
from .conftest import onboard_business, csrf_of
from .test_pg_devices import get_code, pair


def _setup(platform, monkeypatch, name="Rosewood Relay"):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import User
    owner_email = name.lower().replace(" ", "-") + "@example.test"
    owner, org_id, _ = onboard_business(
        platform, business=name, owner_email=owner_email)
    with session_scope() as db:
        user_id = db.scalar(select(User.id).where(User.email == owner_email))
    assert isinstance(user_id, uuid.UUID)
    code = get_code(owner, "Synthetic owner workstation", access="work")
    computer, paired = pair(platform, code)
    assert paired.status_code == 201
    parsed = paired.json()
    assert owner.post(
        f"/app/computers/{parsed['device_id']}/confirm",
        data={"csrf": csrf_of(owner), "check_number": parsed["check_number"]},
    ).status_code == 303
    monkeypatch.setenv(device_work.KEY_NAME, Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(device_work, "linked_subject_user_id", lambda *a: user_id)
    bearer = SimpleNamespace(
        identity=SimpleNamespace(
            issuer="https://issuer.example.test/", audience="https://resource.example.test/mcp",
            subject="synthetic-subject", scopes=frozenset({"mf:read", "mf:computer.write"}),
        ), client_id="https://chatgpt.com/oauth/client.json", issued_at=1000000000,
    )
    return owner, org_id, user_id, computer, parsed, bearer


def _enqueue(org_id, device_id, bearer, *, content="real change", request_key="relay-test-key-0001"):
    return device_work.enqueue(
        bearer, issuer="https://issuer.example.test/",
        audience="https://resource.example.test/mcp",
        org_id=str(org_id), device_id=str(device_id),
        action="write_file", project_id="a" * 24,
        relative_path="site/index.html", content=content,
        expected_sha256=None, request_key=request_key,
    )


def test_real_pg_encrypted_work_lease_and_result_receipt(platform, monkeypatch):
    from app.db import session_scope
    owner, org, user, computer, linked, bearer = _setup(platform, monkeypatch)
    job = _enqueue(org, linked["device_id"], bearer)
    assert job["state"] == "queued" and job["already_exists"] is False
    assert _enqueue(org, linked["device_id"], bearer)["already_exists"] is True
    with pytest.raises(device_work.WorkQueueDenied):
        _enqueue(org, linked["device_id"], bearer, content="different")
    job_id = uuid.UUID(job["job_id"])
    with session_scope(org_id=org) as db:
        stored = db.get(DeviceWorkRequest, job_id)
        assert stored.status == "queued"
        assert b"real change" not in stored.payload_encrypted
        assert b"site/index.html" not in stored.payload_encrypted
        assert stored.payload_encrypted.startswith(b"gAAAA")
    claim_response = computer.post(
        "/api/device/v1/work/claim",
        headers={"Authorization": "Bearer " + linked["credential"]})
    assert claim_response.status_code == 200, claim_response.text
    claimed = claim_response.json()["job"]
    assert claimed["job_id"] == job["job_id"]
    assert claimed["payload"]["content"] == "real change"
    assert claimed["payload"]["project_id"] == "a" * 24
    bad = computer.post(
        f"/api/device/v1/work/{job['job_id']}/finish",
        headers={"Authorization": "Bearer " + linked["credential"]},
        json={"lease_token": "invalid" * 8, "succeeded": True,
              "result_sha256": hashlib.sha256(b"real change").hexdigest()})
    assert bad.status_code == 403
    finished = computer.post(
        f"/api/device/v1/work/{job['job_id']}/finish",
        headers={"Authorization": "Bearer " + linked["credential"]},
        json={"lease_token": claimed["lease_token"], "succeeded": True,
              "result_sha256": hashlib.sha256(b"real change").hexdigest()})
    assert finished.status_code == 200, finished.text
    assert finished.json()["state"] == "succeeded"
    assert device_work.status(
        bearer, issuer="https://issuer.example.test/",
        audience="https://resource.example.test/mcp",
        org_id=str(org), job_id=job["job_id"])["result_sha256"] == hashlib.sha256(b"real change").hexdigest()
    assert computer.post(
        f"/api/device/v1/work/{job['job_id']}/finish",
        headers={"Authorization": "Bearer " + linked["credential"]},
        json={"lease_token": claimed["lease_token"], "succeeded": True,
              "result_sha256": hashlib.sha256(b"real change").hexdigest()}).status_code == 403
    with session_scope(org_id=org) as db:
        assert db.get(DeviceWorkRequest, job_id).payload_encrypted == b""


def test_scope_tenant_and_device_revocation_denied(platform, monkeypatch):
    a, org_a, user_a, computer_a, linked_a, bearer_a = _setup(platform, monkeypatch, "Evergreen Work A")
    _, _, _, _, linked_b, _ = _setup(platform, monkeypatch, "Evergreen Work B")
    monkeypatch.setattr(device_work, "linked_subject_user_id", lambda *args: user_a)
    with pytest.raises(device_work.WorkQueueDenied):
        _enqueue(org_a, linked_b["device_id"], bearer_a)
    no_scope = SimpleNamespace(
        identity=SimpleNamespace(
            issuer=bearer_a.identity.issuer, audience=bearer_a.identity.audience,
            scopes=frozenset({"mf:read"}), subject=bearer_a.identity.subject),
        issued_at=bearer_a.issued_at, client_id=bearer_a.client_id)
    with pytest.raises(ConnectorAccessDenied):
        _enqueue(org_a, linked_a["device_id"], no_scope)
    queued = _enqueue(org_a, linked_a["device_id"], bearer_a)
    assert a.post(
        f"/app/computers/{linked_a['device_id']}/disconnect",
        data={"csrf": csrf_of(a)}).status_code == 303
    denied = computer_a.post(
        "/api/device/v1/work/claim",
        headers={"Authorization": "Bearer " + linked_a["credential"]})
    assert denied.status_code == 401
    from app.db import session_scope
    with session_scope(org_id=org_a) as db:
        cancelled = db.get(DeviceWorkRequest, uuid.UUID(queued["job_id"]))
        assert cancelled.status == "cancelled"
        assert cancelled.payload_encrypted == b""
        assert cancelled.lease_hash is None


def test_real_postgres_http_cloud_queue_to_local_file_to_receipt(platform, monkeypatch, tmp_path):
    """Authenticated device bearer fetches a queued OAuth-owned write and persists it."""
    from app.local_connect.store import LocalConnect
    from app.paired_connect.registry import PairingRegistry
    from app.desktop_companion.work_poller import WorkPoller
    from app.account_link.client import DeviceApi
    from app.account_link.runtime_guard import AccountRuntimeGuard
    import app.account_link.client as client_module

    owner, org, user, computer, linked, bearer = _setup(
        platform, monkeypatch, "Cedar End To End")
    root = tmp_path / "approved-project"
    root.mkdir()
    local = LocalConnect(tmp_path / "private")
    project_id = local.grant("Website", root, "readwrite")["id"]
    registry = PairingRegistry(local.home)
    pair = registry.create(
        "Owner explicitly approved cloud Work session",
        [project_id], owner_confirmed=True,
        allow_writes=True, owner_confirmed_write=True, expires_minutes=15)
    monkeypatch.setattr(AccountRuntimeGuard, "check", lambda self, *, action: None)
    monkeypatch.setattr(client_module, "resolve_trusted_origin", lambda origin: origin)
    class Account:
        secrets = SimpleNamespace(load=lambda: linked["credential"])
        def refresh(self):
            return SimpleNamespace(status="active", access="work", server_url="http://localhost")
    queued = device_work.enqueue(
        bearer, issuer="https://issuer.example.test/",
        audience="https://resource.example.test/mcp",
        org_id=str(org), device_id=linked["device_id"],
        action="write_file", project_id=project_id,
        relative_path="index.html", content="<h1>Made by connected ChatGPT work</h1>",
        expected_sha256=None, request_key="cloud-to-real-file-0001")
    worker = WorkPoller(
        pairing_id=pair["pairing_id"], registry=registry, account=Account(),
        api_factory=lambda origin: DeviceApi(origin, client=computer))
    assert worker.once() is True
    target = root / "index.html"
    assert target.read_text() == "<h1>Made by connected ChatGPT work</h1>"
    status = device_work.status(
        bearer, issuer="https://issuer.example.test/",
        audience="https://resource.example.test/mcp",
        org_id=str(org), job_id=queued["job_id"])
    assert status["state"] == "succeeded"
    assert status["result_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert worker.once() is False  # already acknowledged, no duplicate job

def test_owner_cancel_queued_work_revokes_future_device_claim(platform, monkeypatch):
    from app.db import session_scope
    owner, org, user, computer, linked, bearer = _setup(
        platform, monkeypatch, "Hickory Cancel")
    job = _enqueue(org, linked["device_id"], bearer, request_key="owner-cancel-001")
    bad_csrf = owner.post(
        f"/app/computers/work/{job['job_id']}/cancel", data={"csrf": "wrong"})
    assert bad_csrf.status_code == 403
    success = owner.post(
        f"/app/computers/work/{job['job_id']}/cancel",
        data={"csrf": csrf_of(owner)})
    assert success.status_code == 303
    with session_scope(org_id=org) as db:
        row = db.get(DeviceWorkRequest, uuid.UUID(job["job_id"]))
        assert row.status == "cancelled"
        assert row.payload_encrypted == b""
    assert device_work.status(
        bearer, issuer=bearer.identity.issuer, audience=bearer.identity.audience,
        org_id=str(org), job_id=job["job_id"])["state"] == "cancelled"
    claim = computer.post("/api/device/v1/work/claim", headers={
        "Authorization": "Bearer " + linked["credential"]})
    assert claim.status_code == 200 and claim.json()["job"] is None
    repeat = device_work.cancel(
        bearer, issuer=bearer.identity.issuer, audience=bearer.identity.audience,
        org_id=str(org), job_id=job["job_id"])
    assert repeat["already_exists"] is True


def test_cannot_cancel_while_device_has_active_lease(platform, monkeypatch):
    from app.db import session_scope
    owner, org, user, computer, linked, bearer = _setup(
        platform, monkeypatch, "Juniper Lease")
    job = _enqueue(org, linked["device_id"], bearer, request_key="leased-cancel-001")
    claimed = computer.post("/api/device/v1/work/claim", headers={
        "Authorization": "Bearer " + linked["credential"]})
    assert claimed.status_code == 200 and claimed.json()["job"]
    with pytest.raises(device_work.WorkQueueDenied):
        device_work.cancel(
            bearer, issuer=bearer.identity.issuer, audience=bearer.identity.audience,
            org_id=str(org), job_id=job["job_id"])
    assert owner.post(
        f"/app/computers/{linked['device_id']}/disconnect",
        data={"csrf": csrf_of(owner)}).status_code == 303
    with session_scope(org_id=org) as db:
        row = db.get(DeviceWorkRequest, uuid.UUID(job["job_id"]))
        assert row.status == "cancelled" and row.payload_encrypted == b""


def test_corrupt_ciphertext_denies_claim_and_clears_payload(platform, monkeypatch):
    from app.db import session_scope
    owner, org, user, computer, linked, bearer = _setup(
        platform, monkeypatch, "Maple Cipher")
    job = _enqueue(org, linked["device_id"], bearer, request_key="corrupt-cipher-001")
    with session_scope(org_id=org) as db:
        row = db.get(DeviceWorkRequest, uuid.UUID(job["job_id"]))
        row.payload_encrypted = b"intentionally-invalid-ciphertext"
    claim = computer.post("/api/device/v1/work/claim", headers={
        "Authorization": "Bearer " + linked["credential"]})
    assert claim.status_code == 200 and claim.json()["job"] is None
    with session_scope(org_id=org) as db:
        row = db.get(DeviceWorkRequest, uuid.UUID(job["job_id"]))
        assert row.status == "failed"
        assert row.payload_encrypted == b""
        assert row.result_status == "cannot_decrypt"


def test_owner_approved_work_roster_discovered_without_local_paths(
    platform, monkeypatch,
):
    """Live Postgres RLS: only active owner grants are visible to the owner."""
    from datetime import timedelta
    from app.db import session_scope
    from app.models import DeviceWorkProject, now
    from app.services import devices
    from sqlalchemy import select

    owner, org, actor, computer, linked, full_bearer = _setup(
        platform, monkeypatch, "Owner Roster A")
    _, other_org, _, _, _, _ = _setup(
        platform, monkeypatch, "Owner Roster B")
    manifest = [{"project_id": "c" * 24, "name": "Public website",
                 "access": "readwrite"}]
    posted = computer.post(
        "/api/device/v1/work/projects",
        headers={"Authorization": "Bearer " + linked["credential"]},
        json={"projects": manifest},
    )
    assert posted.status_code == 200, posted.text
    assert posted.json() == {"reported": 1}
    assert "Cache-Control" in posted.headers
    import app.mcp_plugin.links as link_service
    monkeypatch.setattr(link_service, "linked_subject_user_id", lambda *_: actor)
    read_bearer = SimpleNamespace(
        identity=SimpleNamespace(
            issuer=full_bearer.identity.issuer,
            audience=full_bearer.identity.audience,
            subject=full_bearer.identity.subject,
            scopes=frozenset({"mf:read"}),
        ), issued_at=full_bearer.issued_at,
    )

    def read(business):
        return devices.connected_work_computers_for_oauth(
            read_bearer, issuer=read_bearer.identity.issuer,
            audience=read_bearer.identity.audience,
            business_id=str(business),
        )

    roster = read(org)
    assert roster["count"] == 1
    assert roster["computers"][0]["device_id"] == linked["device_id"]
    assert roster["computers"][0]["projects"][0]["project_id"] == "c" * 24
    assert roster["computers"][0]["projects"][0]["name"] == "Public website"
    assert "root" not in repr(roster)
    assert "credential" not in repr(roster)
    with pytest.raises(ConnectorAccessDenied):
        read(other_org)

    bad = computer.post(
        "/api/device/v1/work/projects",
        headers={"Authorization": "Bearer " + linked["credential"]},
        json={"projects": [{"project_id": "e" * 24,
                            "name": "D:\\Secret", "access": "readwrite"}]},
    )
    assert bad.status_code == 422
    assert read(org)["computers"][0]["projects"][0]["name"] == "Public website"
    duplicate = computer.post(
        "/api/device/v1/work/projects",
        headers={"Authorization": "Bearer " + linked["credential"]},
        json={"projects": [manifest[0], manifest[0]]},
    )
    assert duplicate.status_code == 422
    with session_scope(org_id=org) as db:
        item = db.scalar(select(DeviceWorkProject).where(
            DeviceWorkProject.device_id == uuid.UUID(linked["device_id"])))
        assert item is not None
        item.expires_at = now() - timedelta(seconds=1)
    assert read(org)["computers"][0]["projects"] == []

    assert owner.post(
        f"/app/computers/{linked['device_id']}/disconnect",
        data={"csrf": csrf_of(owner)},
    ).status_code == 303
    assert read(org)["computers"] == []


def test_roster_cannot_be_published_by_unconfirmed_or_read_only_device(
    platform, monkeypatch,
):
    owner, org, _, computer, linked, bearer = _setup(
        platform, monkeypatch, "Readonly Roster")
    manifest = {"projects": [{"project_id": "d" * 24,
                              "name": "Unapproved", "access": "readwrite"}]}
    read_code = get_code(owner, "Read-only laptop", access="read")
    read_client, read_data = pair(platform, read_code)
    assert read_data.status_code == 201
    pending = read_client.post(
        "/api/device/v1/work/projects",
        headers={"Authorization": "Bearer " + read_data.json()["credential"]},
        json=manifest,
    )
    assert pending.status_code == 403
    assert owner.post(
        f"/app/computers/{read_data.json()['device_id']}/confirm",
        data={"csrf": csrf_of(owner),
              "check_number": read_data.json()["check_number"]},
    ).status_code == 303
    active_read = read_client.post(
        "/api/device/v1/work/projects",
        headers={"Authorization": "Bearer " + read_data.json()["credential"]},
        json=manifest,
    )
    assert active_read.status_code == 422
