"""MF-032: account-linked trusted computers. Real PostgreSQL, fictional businesses.

The "computer" in these tests is an HTTP client with no cookies that talks only to
/api/device/v1 with a bearer credential, exactly as a desktop companion would.
"""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from .conftest import client_for, csrf_of, last_email_link, login, make_operator, onboard_business

PASSWORD = "correct horse battery staple"
CODE_ON_PAGE = re.compile(r"<strong>([A-Z0-9]{4}-[A-Z0-9]{4})</strong>")


def _bearer(credential: str) -> dict:
    return {"Authorization": "Bearer " + credential}


def get_code(owner, name="Front office PC", access="read") -> str:
    page = owner.post("/app/computers/codes", data={"csrf": csrf_of(owner), "device_name": name, "access": access})
    assert page.status_code == 200, page.text[:300]
    assert page.headers["cache-control"] == "no-store"
    match = CODE_ON_PAGE.search(page.text)
    assert match, "no code shown"
    return match.group(1)


def pair(platform, code: str, device_name="DESKTOP-SYNTH", kind="windows"):
    computer = client_for(platform)
    response = computer.post("/api/device/v1/pair", json={"code": code, "device_name": device_name, "platform": kind})
    return computer, response


def device_id_of(owner_page_text: str) -> str:
    return re.search(r"/app/computers/([0-9a-f-]{36})/(?:confirm|disconnect)", owner_page_text).group(1)


def connect(platform, owner, *, access="read", name="Front office PC"):
    """Full happy path. Returns (computer_client, credential, device_id)."""
    computer, response = pair(platform, get_code(owner, name, access))
    assert response.status_code == 201, response.text
    body = response.json()
    confirmed = owner.post(f"/app/computers/{body['device_id']}/confirm",
                           data={"csrf": csrf_of(owner), "check_number": body["check_number"]})
    assert confirmed.status_code == 303
    return computer, body["credential"], body["device_id"]


def add_member(platform, owner, email, role):
    owner.post("/app/team/invite", data={"csrf": csrf_of(owner), "email": email, "role": role})
    link = last_email_link(email, "/accept-invite")
    member = client_for(platform)
    assert member.post("/accept-invite", data={"token": link.split("token=")[1], "name": role.title(),
                                               "password": PASSWORD}).status_code == 303
    return member


@pytest.fixture(scope="module")
def two(platform):
    a, org_a, _ = onboard_business(platform, business="Alder Devices Co", owner_email="owner@alder-dev.example")
    b, org_b, _ = onboard_business(platform, business="Birch Devices Co", owner_email="owner@birch-dev.example")
    return {"a": a, "org_a": org_a, "b": b, "org_b": org_b}


# ------------------------------------------------------------------------- happy path

def test_connect_confirm_use_and_disconnect(platform, two):
    from app.db import session_scope
    from app.models import AuditEvent, ConnectedDevice
    from app.security import digest
    owner = two["a"]
    code = get_code(owner, "Front office PC", "suggest")
    computer, response = pair(platform, code.lower().replace("-", " "))        # owners type codes loosely
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    body = response.json()
    credential, check = body["credential"], body["check_number"]
    assert body["status"] == "pending" and re.fullmatch(r"\d{6}", check)
    assert credential.startswith("mfd1." + two["org_a"].hex + ".")

    # Pending: the computer may ask its status and nothing else.
    assert computer.get("/api/device/v1/status", headers=_bearer(credential)).json()["status"] == "pending"
    for path in ("/api/device/v1/heartbeat", "/api/device/v1/rotate"):
        assert computer.post(path, headers=_bearer(credential)).status_code == 403
    event = {"action": "file_read", "result": "ok", "project_label": "My Website", "count": 3,
             "occurred_at": datetime.now(timezone.utc).isoformat()}
    assert computer.post("/api/device/v1/activity", headers=_bearer(credential), json={"events": [event]}).status_code == 403

    # The owner sees it waiting, with the name they chose and the name the computer claims.
    page = owner.get("/app/computers").text
    assert "Waiting for you" in page and "Front office PC" in page and "DESKTOP-SYNTH" in page
    assert check not in page and credential not in page                        # the page never reveals either
    device_id = device_id_of(page)
    assert device_id == body["device_id"]
    assert owner.post(f"/app/computers/{device_id}/confirm",
                      data={"csrf": csrf_of(owner), "check_number": check[:3] + " " + check[3:]}).status_code == 303

    status = computer.get("/api/device/v1/status", headers=_bearer(credential)).json()
    assert status == {"device_id": device_id, "status": "active", "access": "suggest",
                      "access_label": "Suggest changes", "business": "Alder Devices Co", "name": "Front office PC"}
    assert computer.post("/api/device/v1/heartbeat", headers=_bearer(credential)).status_code == 200
    events = [event, {**event, "action": "change_proposed", "count": 1}, {**event, "action": "change_approved", "count": 1}]
    assert computer.post("/api/device/v1/activity", headers=_bearer(credential), json={"events": events}).json() == {"stored": 3}
    page = owner.get("/app/computers").text
    assert "Connected" in page and "Files read" in page and "Change approved by you" in page and "My Website" in page

    # Only hashes are stored, and the audit trail has the story without any secret in it.
    with session_scope(org_id=two["org_a"]) as db:
        device = db.get(ConnectedDevice, uuid.UUID(device_id))
        assert device.credential_hash == digest(credential) and device.check_number == "" and device.last_seen_at
        assert device.confirmed_by is not None
        audit_rows = db.execute(select(AuditEvent.action, AuditEvent.meta).where(AuditEvent.action.like("device.%"))).all()
        assert {"device.code_created", "device.pairing_requested", "device.confirmed"} <= {a for a, _ in audit_rows}
        stored = " ".join(str(m) for _, m in audit_rows) + " " + " ".join(
            r[0] for r in db.execute(text("SELECT coalesce(code_hash,'') FROM device_pairing_codes")))
        assert credential not in stored and code.replace("-", "") not in stored and check not in stored

    # Disconnect now: the very next request is refused, and stays refused.
    assert owner.post(f"/app/computers/{device_id}/disconnect", data={"csrf": csrf_of(owner)}).status_code == 303
    for method, path in (("get", "/api/device/v1/status"), ("post", "/api/device/v1/heartbeat"), ("post", "/api/device/v1/rotate")):
        assert getattr(computer, method)(path, headers=_bearer(credential)).status_code == 401, path
    assert "Disconnected" in owner.get("/app/computers").text
    with session_scope(org_id=two["org_a"]) as db:
        device = db.get(ConnectedDevice, uuid.UUID(device_id))
        assert device.status == "revoked" and device.credential_hash != digest(credential)   # old hash is gone too


# ---------------------------------------------------------------------- pairing codes

def test_codes_are_single_use_expiring_and_never_stored(platform, two):
    from app.db import session_scope
    from app.models import DevicePairingCode, now
    owner = two["a"]
    code = get_code(owner, "Single Use PC")
    _, first = pair(platform, code)
    assert first.status_code == 201
    _, again = pair(platform, code)
    assert again.status_code == 400                                             # single use
    for wrong in ("ZZZZ-ZZZZ", "ABCD", "' OR 1=1 --", code[:-1] + ("A" if code[-1] != "A" else "B")):
        _, response = pair(platform, wrong)
        assert response.status_code in (400, 422), wrong
        assert "credential" not in response.text
    expiring = get_code(owner, "Expiring PC")
    with session_scope() as db:
        db.execute(text("UPDATE device_pairing_codes SET expires_at = now() - interval '1 second' WHERE used_at IS NULL"))
        assert db.scalar(select(DevicePairingCode.id).where(DevicePairingCode.expires_at > now(),
                                                            DevicePairingCode.used_at.is_(None))) is None
    _, expired = pair(platform, expiring)
    assert expired.status_code == 400 and expired.json() == again.json()        # identical answer for every failure
    with session_scope() as db:
        hashes = [r[0] for r in db.execute(text("SELECT code_hash FROM device_pairing_codes"))]
        assert all(len(h) == 64 for h in hashes) and code.replace("-", "") not in " ".join(hashes)
    # A cancelled code cannot be redeemed.
    cancelled = get_code(owner, "Cancelled PC")
    code_id = re.search(r"/app/computers/codes/([0-9a-f-]{36})/cancel", owner.get("/app/computers").text).group(1)
    assert owner.post(f"/app/computers/codes/{code_id}/cancel", data={"csrf": csrf_of(owner)}).status_code == 303
    assert pair(platform, cancelled)[1].status_code == 400
    # Unknown fields, unknown platforms and oversized names are rejected by the typed contract.
    computer = client_for(platform)
    for bad in ({"code": get_code(owner, "Typed PC"), "device_name": "x", "platform": "amiga"},
                {"code": "ABCD-EFGH", "device_name": "x", "platform": "windows", "org_id": str(two["org_b"])},
                {"code": "ABCD-EFGH", "device_name": "n" * 500, "platform": "windows"}):
        assert computer.post("/api/device/v1/pair", json=bad).status_code == 422


def test_a_redeemed_code_is_useless_without_the_owners_confirmation(platform, two):
    """The attack this defends against: someone learns or guesses a code and redeems it on
    THEIR computer. Their check number is not the one the owner sees on the owner's machine."""
    from app.db import session_scope
    from app.models import ConnectedDevice
    owner = two["a"]
    attacker, response = pair(platform, get_code(owner, "Back office PC"), device_name="ATTACKER-PC")
    body = response.json()
    credential, device_id = body["credential"], body["device_id"]
    wrong = "000000" if body["check_number"] != "000000" else "111111"
    for attempt in range(5):
        guess = owner.post(f"/app/computers/{device_id}/confirm", data={"csrf": csrf_of(owner), "check_number": wrong})
        assert guess.status_code == 303 and "mf_notice" in guess.headers["location"]
        assert attacker.post("/api/device/v1/heartbeat", headers=_bearer(credential)).status_code in (401, 403)
    # Five wrong numbers end the attempt for good, even with the right number afterwards.
    owner.post(f"/app/computers/{device_id}/confirm", data={"csrf": csrf_of(owner), "check_number": body["check_number"]})
    assert attacker.get("/api/device/v1/status", headers=_bearer(credential)).status_code == 401
    with session_scope(org_id=two["org_a"]) as db:
        device = db.get(ConnectedDevice, uuid.UUID(device_id))
        assert device.status == "expired" and device.confirmed_at is None

    # A computer nobody confirms within the window expires by itself.
    idle, response = pair(platform, get_code(owner, "Idle PC"))
    idle_body = response.json()
    with session_scope(org_id=two["org_a"]) as db:
        db.get(ConnectedDevice, uuid.UUID(idle_body["device_id"])).created_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    assert idle.get("/api/device/v1/status", headers=_bearer(idle_body["credential"])).status_code == 401
    late = owner.post(f"/app/computers/{idle_body['device_id']}/confirm",
                      data={"csrf": csrf_of(owner), "check_number": idle_body["check_number"]})
    assert late.status_code == 303
    assert idle.get("/api/device/v1/status", headers=_bearer(idle_body["credential"])).status_code == 401


def test_concurrent_redemption_creates_exactly_one_computer(platform, two):
    code = get_code(two["a"], "Raced PC")
    results, barrier = [], threading.Barrier(5)

    def redeem():
        client = client_for(platform)
        barrier.wait()
        results.append(client.post("/api/device/v1/pair", json={"code": code, "device_name": "R", "platform": "windows"}).status_code)

    threads = [threading.Thread(target=redeem) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 400, 400, 400, 400], results


# ------------------------------------------------------------------------ credentials

def test_credential_rotation_and_forgery(platform, two):
    computer, credential, device_id = connect(platform, two["a"], name="Rotation PC")
    rotated = computer.post("/api/device/v1/rotate", headers=_bearer(credential))
    assert rotated.status_code == 200 and rotated.headers["cache-control"] == "no-store"
    new_credential = rotated.json()["credential"]
    assert new_credential != credential
    assert computer.get("/api/device/v1/status", headers=_bearer(credential)).status_code == 401     # old one dies
    assert computer.get("/api/device/v1/status", headers=_bearer(new_credential)).status_code == 200
    prefix, org_hex, secret = new_credential.split(".")
    forged = [
        "", "Bearer", "mfd1", "mfd1..", new_credential + "x", new_credential[:-1],
        f"{prefix}.{two['org_b'].hex}.{secret}",                    # right secret, another tenant
        f"{prefix}.{'0' * 32}.{secret}", f"{prefix}.not-hex.{secret}", f"mfd2.{org_hex}.{secret}",
        f"{prefix}.{org_hex}.{'A' * 43}", "x" * 5000, f"{prefix}.{org_hex}.{secret}.extra",
    ]
    for value in forged:
        response = computer.get("/api/device/v1/status", headers={"Authorization": "Bearer " + value})
        assert response.status_code == 401, value[:40]
        assert response.headers.get("www-authenticate") is None or "Bearer" in response.headers["www-authenticate"]
    for header in ({}, {"Authorization": "Basic " + new_credential}, {"Authorization": new_credential},
                   {"X-Device-Credential": new_credential}):
        assert computer.get("/api/device/v1/status", headers=header).status_code == 401
    # The credential is never accepted from a query string or a body.
    assert computer.get("/api/device/v1/status?credential=" + new_credential).status_code == 401
    # The computer can disconnect itself; after that its own credential is dead.
    assert computer.post("/api/device/v1/disconnect", headers=_bearer(new_credential)).json() == {"status": "revoked"}
    assert computer.get("/api/device/v1/status", headers=_bearer(new_credential)).status_code == 401


def test_people_and_computers_cannot_use_each_others_credentials(platform, two):
    """A signed-in browser session is not a device, and a device credential opens no page."""
    owner = two["a"]
    computer, credential, device_id = connect(platform, owner, name="Boundary PC")
    # Owner's cookies on the device API: ignored.
    assert owner.get("/api/device/v1/status").status_code == 401
    assert owner.post("/api/device/v1/heartbeat").status_code == 401
    # Device credential on workspace routes: it is not a session.
    for path in ("/app", "/app/computers", "/app/inquiries"):
        assert computer.get(path, headers=_bearer(credential)).status_code == 303
    assert computer.get("/api/v1/leads", headers=_bearer(credential)).status_code == 401
    assert computer.post(f"/app/computers/{device_id}/disconnect", headers=_bearer(credential), data={}).status_code in (303, 403)
    assert computer.get("/api/device/v1/status", headers=_bearer(credential)).status_code == 200    # still connected
    # The device API does not exist on customer-site hostnames.
    site = client_for(platform, "anything.sites.localhost")
    assert site.get("/api/device/v1/status", headers=_bearer(credential)).status_code == 404


# ------------------------------------------------------------------- tenant isolation

def test_business_a_cannot_see_confirm_or_disconnect_business_bs_computer(platform, two):
    from app.db import session_scope
    from app.models import ConnectedDevice
    a, b = two["a"], two["b"]
    computer_b, credential_b, device_b = connect(platform, b, name="BIRCH-CANARY PC", access="suggest")
    computer_b.post("/api/device/v1/activity", headers=_bearer(credential_b), json={"events": [{
        "action": "folder_shared", "result": "ok", "project_label": "BIRCH-CANARY project", "count": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat()}]})
    pending_b, response = pair(platform, get_code(b, "BIRCH-CANARY pending"))
    pending = response.json()

    page = a.get("/app/computers").text
    assert "BIRCH-CANARY" not in page and "Birch" not in page
    csrf = csrf_of(a)
    assert a.post(f"/app/computers/{device_b}/disconnect", data={"csrf": csrf}).status_code == 404
    assert a.post(f"/app/computers/{pending['device_id']}/confirm",
                  data={"csrf": csrf, "check_number": pending["check_number"]}).status_code == 404
    b_code_id = re.search(r"/app/computers/codes/([0-9a-f-]{36})/cancel", get_and_page(b)).group(1)
    assert a.post(f"/app/computers/codes/{b_code_id}/cancel", data={"csrf": csrf}).status_code == 404
    assert "BIRCH-CANARY" not in a.get("/app/settings/export").text
    assert "BIRCH-CANARY" not in a.get("/app/activity").text
    # B is untouched.
    assert computer_b.get("/api/device/v1/status", headers=_bearer(credential_b)).json()["business"] == "Birch Devices Co"
    assert pending_b.get("/api/device/v1/status", headers=_bearer(pending["credential"])).json()["status"] == "pending"

    # Database layer: forced row-level security on both new tables.
    with session_scope() as db:
        for table in ("connected_devices", "device_events"):
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
            state = db.execute(text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"), {"t": table}).one()
            assert tuple(state) == (True, True)
    with session_scope(org_id=two["org_a"]) as db:
        assert db.execute(text("SELECT count(*) FROM connected_devices WHERE org_id = :b"), {"b": two["org_b"]}).scalar() == 0
        assert db.execute(text("SELECT count(*) FROM device_events WHERE project_label LIKE 'BIRCH%'")).scalar() == 0
        assert db.execute(text("UPDATE connected_devices SET status = 'revoked' WHERE id = :d"), {"d": device_b}).rowcount == 0
        assert db.get(ConnectedDevice, uuid.UUID(device_b)) is None
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="row-level security"):
        with session_scope(org_id=two["org_a"]) as db:
            db.execute(text("INSERT INTO connected_devices (id, org_id, name, platform, access, status, credential_hash) "
                            "VALUES (gen_random_uuid(), :b, 'planted', 'windows', 'suggest', 'active', :h)"),
                       {"b": two["org_b"], "h": "f" * 64})
    # A computer of A reporting activity can never write into B.
    computer_a, credential_a, _ = connect(platform, a, name="Alder PC")
    computer_a.post("/api/device/v1/activity", headers=_bearer(credential_a), json={"events": [{
        "action": "file_read", "result": "ok", "project_label": "ALDER-MARK", "count": 1,
        "occurred_at": datetime.now(timezone.utc).isoformat()}]})
    assert "ALDER-MARK" not in b.get("/app/computers").text and "ALDER-MARK" in a.get("/app/computers").text


def get_and_page(owner) -> str:
    get_code(owner, "Extra code holder")
    return owner.get("/app/computers").text


# ----------------------------------------------------------------- roles, CSRF, limits

def test_only_owners_and_admins_can_connect_or_disconnect(platform, two):
    owner = two["a"]
    staff = add_member(platform, owner, "staff@alder-dev.example", "staff")
    reader = add_member(platform, owner, "reader@alder-dev.example", "readonly")
    admin = add_member(platform, owner, "admin@alder-dev.example", "admin")
    _, _, device_id = connect(platform, owner, name="Role PC")
    for member in (staff, reader):
        page = member.get("/app/computers")
        assert page.status_code == 200 and "Role PC" in page.text and "Get a connection code" not in page.text
        csrf = csrf_of(member)
        assert member.post("/app/computers/codes", data={"csrf": csrf, "device_name": "Sneaky", "access": "suggest"}).status_code == 403
        assert member.post(f"/app/computers/{device_id}/disconnect", data={"csrf": csrf}).status_code == 403
        assert member.post(f"/app/computers/{device_id}/confirm", data={"csrf": csrf, "check_number": "123456"}).status_code == 403
    assert CODE_ON_PAGE.search(admin.post("/app/computers/codes", data={
        "csrf": csrf_of(admin), "device_name": "Admin PC", "access": "read"}).text)
    # CSRF and cross-origin.
    assert owner.post("/app/computers/codes", data={"device_name": "No CSRF", "access": "read"}).status_code == 403
    assert owner.post(f"/app/computers/{device_id}/disconnect", data={"csrf": "0" * 64}).status_code == 403
    assert owner.post(f"/app/computers/{device_id}/disconnect", data={"csrf": csrf_of(owner)},
                      headers={"Origin": "https://evil.example"}).status_code == 403
    # Operators have no workspace, so no access; anonymous visitors are sent to sign in.
    op = login(platform, *make_operator())
    assert op.get("/app/computers").status_code == 403
    assert client_for(platform).get("/app/computers").status_code == 303
    # Invalid input is explained, not stored.
    for data in ({"device_name": "", "access": "read"}, {"device_name": "Fine", "access": "admin"}):
        response = owner.post("/app/computers/codes", data={"csrf": csrf_of(owner), **data})
        assert response.status_code == 303 and "mf_notice" in response.headers["location"]


def test_activity_is_metadata_only_and_respects_read_only(platform, two):
    from app.db import session_scope
    computer, credential, device_id = connect(platform, two["a"], access="read", name="Read Only PC")
    when = datetime.now(timezone.utc).isoformat()

    def send(**event):
        base = {"action": "file_read", "result": "ok", "project_label": "Site", "count": 1, "occurred_at": when}
        return computer.post("/api/device/v1/activity", headers=_bearer(credential), json={"events": [{**base, **event}]})

    assert send().status_code == 200
    # Anything shaped like a file location is refused, so paths never reach the server.
    for label in ("C:\\Users\\Dana\\Website", "/home/dana/site", "..\\..\\secrets", "D:", "site\x00name"):
        assert send(project_label=label).status_code == 422, label
    # A Read-only computer cannot claim it made or applied a change.
    assert send(action="change_proposed").status_code == 422
    assert send(action="change_approved").status_code == 422
    assert send(action="change_proposed", result="denied").status_code == 200          # reporting a refusal is fine
    # The typed contract rejects unknown actions, extra fields, content and oversized batches.
    for bad in ({"action": "shell_executed"}, {"result": "maybe"}, {"count": -1}, {"count": 10_001},
                {"content": "file body"}, {"path": "index.html"}, {"project_label": "x" * 81}, {"occurred_at": "yesterday"}):
        assert send(**bad).status_code == 422, bad
    base = {"action": "file_read", "result": "ok", "project_label": "Site", "count": 1, "occurred_at": when}
    assert computer.post("/api/device/v1/activity", headers=_bearer(credential), json={"events": [base] * 51}).status_code == 422
    assert computer.post("/api/device/v1/activity", headers=_bearer(credential), json={"events": []}).status_code == 422
    # A timestamp from the far future or past is replaced with the server's clock.
    assert send(occurred_at="2099-01-01T00:00:00+00:00").status_code == 200
    with session_scope(org_id=two["org_a"]) as db:
        newest = db.execute(text("SELECT max(occurred_at) FROM device_events")).scalar()
        assert newest < datetime.now(timezone.utc) + timedelta(minutes=6)
        columns = {r[0] for r in db.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'device_events'"))}
        assert columns == {"id", "org_id", "device_id", "action", "result", "project_label", "count", "occurred_at", "created_at"}


def test_limits_and_throttling(platform, two):
    from app.db import session_scope
    owner, _, _ = onboard_business(platform, business="Limits Devices Co", owner_email="owner@limits-dev.example")
    for n in range(5):
        get_code(owner, f"PC {n}")
    sixth = owner.post("/app/computers/codes", data={"csrf": csrf_of(owner), "device_name": "PC 6", "access": "read"})
    assert sixth.status_code == 303 and not CODE_ON_PAGE.search(sixth.text)
    # Pairing attempts from one client are throttled; the answer never says whether a code was close.
    statuses = [pair(platform, "ZZZZ-ZZZZ")[1].status_code for _ in range(12)]
    assert statuses[:10] == [400] * 10 and statuses[-1] == 429
    with session_scope() as db:
        db.execute(text("DELETE FROM rate_limits"))
    # Repeated bad credentials are throttled too.
    computer = client_for(platform)
    bad = [computer.get("/api/device/v1/status", headers=_bearer("mfd1." + "0" * 32 + "." + "A" * 43)).status_code for _ in range(62)]
    assert bad[0] == 401 and bad[-1] == 429


def test_deleting_a_business_removes_its_computers(platform):
    from app.db import session_scope
    owner, org_id, _ = onboard_business(platform, business="Leaving Devices Co", owner_email="owner@leaving-dev.example")
    computer, credential, _ = connect(platform, owner, name="Leaving PC")
    get_code(owner, "Unused code PC")
    assert owner.post("/app/settings/delete", data={"csrf": csrf_of(owner), "confirm": "Leaving Devices Co"}).status_code == 303
    assert computer.get("/api/device/v1/status", headers=_bearer(credential)).status_code == 401
    with session_scope() as db:
        assert db.execute(text("SELECT count(*) FROM device_pairing_codes WHERE org_id = :o"), {"o": org_id}).scalar() == 0
    with session_scope(org_id=org_id) as db:
        assert db.execute(text("SELECT count(*) FROM connected_devices")).scalar() == 0


def test_work_mode_requires_owner_confirmation_and_tracks_real_action_metadata(platform):
    """Database-backed Work-mode permission is separate from read/suggest."""
    owner, _, _ = onboard_business(
        platform, business="Copper Work Mode", owner_email="owner@copper-workmode.example",
    )
    code = get_code(owner, "Production work desktop", access="work")
    computer, pairing = pair(platform, code)
    assert pairing.status_code == 201
    key, device_id = pairing.json()["credential"], pairing.json()["device_id"]
    waiting = computer.get("/api/device/v1/status", headers=_bearer(key)).json()
    assert waiting["status"] == "pending" and waiting["access"] == "work"
    # Pairing alone never authorizes writing.
    event = {
        "action": "file_written", "result": "ok", "project_label": "Company Website",
        "count": 1, "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    assert computer.post(
        "/api/device/v1/activity", headers=_bearer(key), json={"events": [event]}
    ).status_code == 403
    assert owner.post(
        f"/app/computers/{device_id}/confirm",
        data={"csrf": csrf_of(owner), "check_number": pairing.json()["check_number"]},
    ).status_code == 303
    assert computer.get("/api/device/v1/status", headers=_bearer(key)).json()[
        "access_label"] == "Work in approved projects"
    assert computer.post(
        "/api/device/v1/activity", headers=_bearer(key), json={"events": [event]}
    ).json() == {"stored": 1}
    page = owner.get("/app/computers").text
    assert "File created or updated" in page
    assert "Work in approved projects" in page
    assert owner.post(
        f"/app/computers/{device_id}/disconnect", data={"csrf": csrf_of(owner)}
    ).status_code == 303
    assert computer.post(
        "/api/device/v1/activity", headers=_bearer(key), json={"events": [event]}
    ).status_code == 401
