"""Real PostgreSQL referential-integrity gates (disposable test database only)."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _org_and_site(suffix: str):
    from app.db import session_scope
    from app.models import Organization, Site
    org_id = uuid.uuid4()
    slug = "synthetic-fk-" + suffix + "-" + uuid.uuid4().hex[:8]
    with session_scope(org_id=org_id) as db:
        db.add(Organization(id=org_id, name="Synthetic Ref Test " + suffix))
        db.flush()
        site = Site(org_id=org_id, slug=slug, draft={"name": "Synthetic"})
        db.add(site)
        db.flush()
        site_id = site.id
    return org_id, site_id, slug


def test_referential_constraints_exist_in_real_database(pg_database):
    from app.db import get_engine
    wanted = {
        "uq_sites_id_org_id", "fk_site_hosts_site_owner",
        "fk_leads_site", "fk_leads_assignee", "fk_leads_customer",
        "fk_service_orders_org", "fk_sites_published_version",
    }
    with get_engine().connect() as conn:
        names = {r[0] for r in conn.execute(text(
            "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace"))}
    assert wanted.issubset(names), sorted(wanted - names)


def test_hostname_cannot_claim_another_tenants_site(pg_database):
    from app.db import session_scope
    from app.models import SiteHost
    org_a, site_a, slug = _org_and_site("owner")
    org_b, _, _ = _org_and_site("other")
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(SiteHost(hostname=slug + ".sites.localhost", site_id=site_a,
                            org_id=org_b, kind="subdomain"))
            db.flush()
    with session_scope() as db:
        assert db.get(SiteHost, slug + ".sites.localhost") is None
        db.add(SiteHost(hostname=slug + ".sites.localhost", site_id=site_a,
                        org_id=org_a, kind="subdomain"))
    with session_scope() as db:
        assert db.get(SiteHost, slug + ".sites.localhost") is not None


def test_deleting_site_cascades_hostname_but_retains_inquiry(pg_database):
    from app.db import session_scope
    from app.models import Lead, Site, SiteHost
    org_id, site_id, slug = _org_and_site("cascade")
    hostname = slug + ".sites.localhost"
    with session_scope(org_id=org_id) as db:
        lead = Lead(org_id=org_id, site_id=site_id, name="Synthetic Contact",
                    email="fake@example.invalid", message="Retained inquiry")
        db.add(lead)
        db.flush()
        lead_id = lead.id
    with session_scope() as db:
        db.add(SiteHost(hostname=hostname, site_id=site_id, org_id=org_id, kind="subdomain"))
    with session_scope(org_id=org_id) as db:
        site = db.get(Site, site_id)
        db.delete(site)
    with session_scope() as db:
        assert db.get(SiteHost, hostname) is None
    with session_scope(org_id=org_id) as db:
        lead = db.get(Lead, lead_id)
        assert lead is not None and lead.site_id is None


def test_invalid_assignee_fails_database_constraint(pg_database):
    from app.db import session_scope
    from app.models import Lead
    org_id, _, _ = _org_and_site("assignee")
    with pytest.raises(IntegrityError):
        with session_scope(org_id=org_id) as db:
            db.add(Lead(org_id=org_id, name="Synthetic", email="fake@example.invalid",
                        message="No account with this ID", assigned_to=uuid.uuid4()))
            db.flush()
