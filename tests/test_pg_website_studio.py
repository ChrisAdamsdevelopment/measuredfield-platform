"""MF-024 customer workspace website Workbench, real isolated PostgreSQL only.

Checks published-version vs draft IFrames, no fake before snapshot, tenant
boundaries, zero mutation from GET, and separate external-OAuth connection state.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import AuditEvent, Site, SiteVersion
from .conftest import client_for, create_and_publish_site, csrf_of, onboard_business


def test_website_studio_no_site_uses_real_empty_state(platform):
    owner, org, _ = onboard_business(
        platform, business="Orchard Empty Workshop",
        owner_email="orchard-empty@example.test")
    visitor = client_for(platform)
    assert visitor.get("/app/website/studio").status_code == 303
    response = owner.get("/app/website/studio")
    assert response.status_code == 200, response.text[:250]
    page = response.text
    assert "Your design workbench starts with a website" in page
    assert "Create website draft" in page
    assert 'id="mf-studio-range"' not in page
    assert 'src="/app/website/preview/"' not in page
    assert "No earlier baseline" not in page
    assert "ChatGPT Connections" in page


def test_studio_real_published_and_draft_snapshot_without_mutation(platform):
    owner, org, _ = onboard_business(
        platform, business="Orchard Actual Designs",
        owner_email="orchard-actual@example.test")
    host, version_id = create_and_publish_site(
        owner, name="Orchard Actual Designs", slug="orchard-actual-designs")
    csrf = csrf_of(owner)
    with session_scope(org_id=org) as db:
        site = db.scalar(select(Site).where(Site.slug == "orchard-actual-designs"))
        assert site is not None
        published_id = site.published_version_id
        before_events = db.scalar(select(AuditEvent).where(
            AuditEvent.action == "site.published")).id

    # Only the working draft changes; public visitors still see the saved version.
    resp = owner.post("/app/website/save", data={
        "csrf": csrf, "name": "Orchard Actual Designs", "city": "Fictionville, AZ",
        "industry": "cleaning", "color": "ocean",
        "email": "contact@orchard.example.test", "phone": "",
        "about": "", "headline": "Better, Brighter, Cleaner",
        "intro": "Seasonal home care", "response_window": "no_promise",
        "services": "Deep cleaning | Dedicated teams",
    })
    assert resp.status_code == 303, resp.text
    response = owner.get("/app/website/studio")
    assert response.status_code == 200, response.text[:300]
    page = response.text
    assert f"/app/website/versions/{version_id}/view/" in page
    assert 'src="/app/website/preview/"' in page
    assert 'id="mf-studio-range"' in page
    assert 'class="mf-studio-frame mf-studio-frame-before"' in page
    assert 'class="mf-studio-frame mf-studio-frame-after"' in page
    assert "Public version is live" in page
    assert "Working draft has changes" in page
    assert "50% before / 50% after" in page
    assert 'sandbox=""' in page
    assert "No completed or pending computer work" in page
    assert "Not linked" in page
    assert "Copy request for ChatGPT" in page

    with session_scope(org_id=org) as db:
        site = db.scalar(select(Site).where(Site.slug == "orchard-actual-designs"))
        assert site.published_version_id == published_id
        version = db.get(SiteVersion, published_id)
        assert version.id == published_id and version.content["headline"] != site.draft["headline"]
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.action == "site.published")).id == before_events


def test_studio_never_shows_another_business_snapshots(platform):
    owner_a, org_a, _ = onboard_business(
        platform, business="Orchard Tenant A", owner_email="orchard-a@example.test")
    owner_b, org_b, _ = onboard_business(
        platform, business="Orchard Tenant B", owner_email="orchard-b@example.test")
    _, b_version_id = create_and_publish_site(
        owner_b, name="Orchard Tenant B", slug="orchard-tenant-b")
    a_page = owner_a.get("/app/website/studio")
    assert a_page.status_code == 200
    assert str(b_version_id) not in a_page.text
    assert "Orchard Tenant B" not in a_page.text
    assert owner_a.get(
        f"/app/website/versions/{b_version_id}/view/").status_code == 404
