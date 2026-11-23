"""Takeoff pilot: same website, account-gated HTML and partitioned local project store."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.deps import get_principal
from app.main import create_main_app
from app.routers import takeoff_pilot

ASSETS = Path(__file__).resolve().parents[1] / "app" / "static" / "takeoff"


def owner(org=None, user=None):
    return SimpleNamespace(
        org=SimpleNamespace(id=org or uuid.uuid4(), name="Andrew's <Construction>"),
        user=SimpleNamespace(id=user or uuid.uuid4(), name="Tester & Owner"),
        can=lambda permission: permission == "read",
    )


def test_disabled_takeoff_is_unlisted_and_inaccessible(monkeypatch):
    monkeypatch.delenv("MF_TAKEOFF_PILOT_ENABLED", raising=False)
    app = create_main_app()
    app.dependency_overrides[get_principal] = lambda: None
    with TestClient(app) as client:
        r = client.get("/takeoff", follow_redirects=False)
        assert r.status_code == 404
        assert client.get("/api/capabilities").status_code == 404


def test_enabled_takeoff_still_requires_login_and_business_membership(monkeypatch):
    org = uuid.uuid4()
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED", "true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS", str(org))
    app = create_main_app()
    app.dependency_overrides[get_principal] = lambda: None
    with TestClient(app) as client:
        r = client.get("/takeoff", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login?next=/takeoff"
    app.dependency_overrides[get_principal] = lambda: owner(uuid.uuid4())
    with TestClient(app) as client:
        assert client.get("/takeoff", follow_redirects=False).status_code == 404
    p = owner(org)
    p.can = lambda _: False
    app.dependency_overrides[get_principal] = lambda: p
    with TestClient(app) as client:
        assert client.get("/takeoff", follow_redirects=False).status_code == 403


def test_account_scoped_html_uses_real_guided_app_with_strict_csp(monkeypatch):
    org = uuid.uuid4()
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ENABLED", "true")
    monkeypatch.setenv("MF_TAKEOFF_PILOT_ORG_IDS", str(org))
    p = owner(org)
    app = create_main_app()
    app.dependency_overrides[get_principal] = lambda: p
    with TestClient(app) as client:
        resp = client.get("/takeoff")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "private, no-store"
        assert resp.headers["x-robots-tag"].startswith("noindex")
        assert "worker-src 'self' blob:" in resp.headers["content-security-policy"]
        assert "wasm-unsafe-eval" in resp.headers["content-security-policy"]
        html = resp.text
        assert '<base href="/static/takeoff/public/guided/">' in html
        assert 'href="/takeoff#content"' in html
        assert "Signed in as" in html
        assert "&lt;Construction&gt;" in html
        assert "Tester &amp; Owner" in html
        assert "ChatGPT" in html
        assert "Download takeoff details" in html
        token = re.search(r'name="mf-takeoff-storage-scope" content="([a-f0-9]{32})"', html)
        assert token is not None
        assert client.get("/takeoff/").status_code == 200
        page = client.get("/static/takeoff/public/guided/guided.mjs")
        assert page.status_code == 200
        assert 'application/javascript' in page.headers["content-type"] or "javascript" in page.headers["content-type"]
        assert "createProject" in page.text
        storage = client.get("/static/takeoff/public/guided/storage.mjs")
        assert storage.status_code == 200
        assert 'mf-takeoff-storage-scope' in storage.text
        assert "measuredfield-remodel-guided-v1-" in storage.text
        worker = client.get("/static/takeoff/public/vendor/pdfjs-5.7.284/pdf.worker.min.mjs")
        assert worker.status_code == 200
        assert len(worker.content) > 1000
        assert client.get("/static/takeoff/src/geometry.mjs").status_code == 200
        assert client.get("/api/interpret-plan-image", follow_redirects=False).status_code in {404, 405}
        cap=client.get("/api/capabilities")
        assert cap.status_code==200
        assert cap.json()["ai_review"] is False
        assert cap.json()["writes"] is False


def test_different_measuredfield_users_or_orgs_get_different_local_databases():
    a = owner()
    b = owner(a.org.id)
    c = owner(uuid.uuid4(), a.user.id)
    assert re.search(r'content="([a-f0-9]{32})"', takeoff_pilot.render_takeoff_page(a)).group(1) != \
           re.search(r'content="([a-f0-9]{32})"', takeoff_pilot.render_takeoff_page(b)).group(1)
    assert re.search(r'content="([a-f0-9]{32})"', takeoff_pilot.render_takeoff_page(a)).group(1) != \
           re.search(r'content="([a-f0-9]{32})"', takeoff_pilot.render_takeoff_page(c)).group(1)


def test_no_private_plans_or_secrets_are_deployed():
    paths = [p for p in ASSETS.rglob("*") if p.is_file()]
    assert 40 <= len(paths) <= 90
    assert not any(p.suffix.lower() in {".pdf", ".env", ".pem", ".key", ".py"} for p in paths)
    forbidden = (b"nvapi-", b"sk-proj-", b"PRIVATE_AUTH", b"CASE_2026_001_BATH")
    for p in paths:
        assert not any(x in p.read_bytes() for x in forbidden), str(p)
    home = (ASSETS.parents[1] / "templates" / "public" / "home.html").read_text(encoding="utf-8")
    assert 'href="/takeoff"' not in home and "href=\'/takeoff\'" not in home
