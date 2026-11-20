"""MF-024 customer workspace website Workbench, real isolated PostgreSQL only.

Checks published-version vs draft IFrames, no fake before snapshot, tenant
boundaries, zero mutation from GET, and separate external-OAuth connection state.
"""
from __future__ import annotations

import pytest
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
    assert "Queue real website work" in page

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

def _website_oauth_user(org_id, owner_email: str, monkeypatch, *, write: bool = True):
    from types import SimpleNamespace
    import uuid
    from app.models import User
    from app.services import website_work
    with session_scope() as db:
        user_id = db.scalar(select(User.id).where(User.email == owner_email))
    assert isinstance(user_id, uuid.UUID)
    monkeypatch.setattr(website_work, "linked_subject_user_id", lambda *args: user_id)
    scopes = {"mf:read", website_work.WRITE_SCOPE} if write else {"mf:read"}
    return SimpleNamespace(
        issued_at=1770000000,
        identity=SimpleNamespace(
            issuer="https://auth0.example.test/",
            audience="https://measuredfield.example.test/mcp",
            subject="verified-user", scopes=frozenset(scopes),
        )
    )


def _website_work_submit(owner, instruction: str = "Improve the homepage hero and call to action"):
    csrf = csrf_of(owner)
    return owner.post("/app/website/studio/requests", data={
        "csrf": csrf, "instruction": instruction,
    })


def _work_args(bearer, org_id, request_id, patch):
    return {
        "bearer": bearer, "issuer": bearer.identity.issuer,
        "audience": bearer.identity.audience,
        "business_id": str(org_id), "request_id": str(request_id),
        "changes": patch,
    }


def test_website_oauth_real_draft_change_after_queued_owner_request(platform, monkeypatch):
    from app.services import website_work
    from app.models import WebsiteWorkRequest
    owner_email = "orchard-oauth@example.test"
    owner, org, _ = onboard_business(
        platform, business="Orchard OAuth Studio", owner_email=owner_email)
    _, published_id = create_and_publish_site(
        owner, name="Orchard OAuth Studio", slug="orchard-oauth")
    owner_post = _website_work_submit(owner)
    assert owner_post.status_code == 303, owner_post.text
    owner_page = owner.get("/app/website/studio")
    assert "Website work queue" in owner_page.text
    assert "Improve the homepage hero" in owner_page.text

    with session_scope(org_id=org) as db:
        task = db.scalar(select(WebsiteWorkRequest).where(
            WebsiteWorkRequest.org_id == org))
        assert task.status == "queued"
        original = db.get(SiteVersion, published_id).content["headline"]
        assert len(task.expected_draft_sha256) == 64
        request_id = task.id

    oauth = _website_oauth_user(org, owner_email, monkeypatch)
    tasks = website_work.list_for_oauth(
        oauth, issuer=oauth.identity.issuer, audience=oauth.identity.audience,
        business_id=str(org))
    assert tasks["work_requests"][0]["id"] == str(request_id)
    assert "mf:read" in oauth.identity.scopes
    read_only = _website_oauth_user(org, owner_email, monkeypatch, write=False)
    with pytest.raises(website_work.ConnectorAccessDenied):
        website_work.apply_for_oauth(**_work_args(
            read_only, org, request_id, {"headline": "Better home care, real results"}))
    patch = {"headline": "Better home care, real results",
             "intro": "More helpful information for customers."}
    result = website_work.apply_for_oauth(**_work_args(
        oauth, org, request_id, patch))
    assert result["status"] == "completed"
    assert result["already_exists"] is False
    assert result["published"] is False
    assert len(result["draft_sha256"]) == 64
    replay = website_work.apply_for_oauth(**_work_args(
        oauth, org, request_id, patch))
    assert replay["already_exists"] is True
    with pytest.raises(website_work.WebsiteWorkDenied):
        website_work.apply_for_oauth(**_work_args(
            oauth, org, request_id, {"headline": "Different payload"}))

    with session_scope(org_id=org) as db:
        site = db.scalar(select(Site).where(Site.org_id == org))
        assert site.draft["headline"] == patch["headline"]
        assert str(site.published_version_id) == str(published_id)
        assert db.get(SiteVersion, published_id).content["headline"] == original
        row = db.get(WebsiteWorkRequest, request_id)
        assert row.status == "completed"
        assert row.completed_by is not None
    page = owner.get("/app/website/studio").text
    assert "Applied draft SHA-256" in page
    assert "50% before / 50% after" in page
    assert "Completed" in page


def test_website_work_stale_draft_and_cancellation(platform, monkeypatch):
    import pytest
    from app.services import website_work
    from app.models import WebsiteWorkRequest
    owner_email = "orchard-stale@example.test"
    owner, org, _ = onboard_business(
        platform, business="Orchard Stale Change", owner_email=owner_email)
    create_and_publish_site(owner, name="Orchard Stale Change", slug="orchard-stale")
    assert owner.post("/app/website/studio/requests", data={
        "csrf": "invalid", "instruction": "Improve our website headline"
    }).status_code == 403
    assert _website_work_submit(owner).status_code == 303
    with session_scope(org_id=org) as db:
        first = db.scalar(select(WebsiteWorkRequest).where(
            WebsiteWorkRequest.org_id == org))
        task_id = first.id
    csrf = csrf_of(owner)
    assert owner.post("/app/website/save", data={
        "csrf": csrf, "name": "Orchard Stale Change", "city": "Fictionville, AZ",
        "industry": "cleaning", "color": "ocean",
        "email": "contact@orchard-stale.example.test", "phone": "",
        "about": "", "headline": "Independent change since task queued",
        "intro": "", "response_window": "no_promise",
        "services": "Deep cleaning",
    }).status_code == 303
    oauth = _website_oauth_user(org, owner_email, monkeypatch)
    with pytest.raises(website_work.WebsiteWorkDenied, match="draft changed"):
        website_work.apply_for_oauth(**_work_args(
            oauth, org, task_id, {"headline": "Attempt to overwrite newer work"}))
    assert owner.post(f"/app/website/studio/requests/{task_id}/cancel", data={
        "csrf": csrf
    }).status_code == 303
    with pytest.raises(website_work.WebsiteWorkDenied, match="Cancelled"):
        website_work.apply_for_oauth(**_work_args(
            oauth, org, task_id, {"headline": "Attempt to overwrite newer work"}))
    with session_scope(org_id=org) as db:
        site = db.scalar(select(Site).where(Site.org_id == org))
        assert site.draft["headline"] == "Independent change since task queued"


def test_cross_business_website_oauth_write_denied(platform, monkeypatch):
    import pytest
    from app.services import website_work
    from app.models import WebsiteWorkRequest
    a_email = "orchard-oauth-a@example.test"
    owner_a, org_a, _ = onboard_business(
        platform, business="Orchard Web A", owner_email=a_email)
    create_and_publish_site(owner_a, name="Orchard Web A", slug="orchard-web-a")
    owner_b, org_b, _ = onboard_business(
        platform, business="Orchard Web B", owner_email="orchard-oauth-b@example.test")
    create_and_publish_site(owner_b, name="Orchard Web B", slug="orchard-web-b")
    assert _website_work_submit(owner_b).status_code == 303
    with session_scope(org_id=org_b) as db:
        task = db.scalar(select(WebsiteWorkRequest).where(
            WebsiteWorkRequest.org_id == org_b))
        task_id = task.id
    oauth_a = _website_oauth_user(org_a, a_email, monkeypatch)
    with pytest.raises(website_work.ConnectorAccessDenied):
        website_work.list_for_oauth(
            oauth_a, issuer=oauth_a.identity.issuer,
            audience=oauth_a.identity.audience, business_id=str(org_b))
    with pytest.raises(website_work.ConnectorAccessDenied):
        website_work.apply_for_oauth(**_work_args(
            oauth_a, org_b, task_id, {"headline": "Cross-business corruption"}))
    with session_scope(org_id=org_b) as db:
        assert db.get(WebsiteWorkRequest, task_id).status == "queued"
