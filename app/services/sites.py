"""Customer website lifecycle: draft -> version -> approval -> publish -> rollback.

Callers must already hold a tenant context on the session; every query here runs under
row-level security, so a site id from another organization simply does not exist.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import func, select, text

from ..config import get_settings
from ..db import TenantSession
from ..models import Lead, Site, SiteHost, SiteVersion, now
from . import sitegen
from .audit import audit

HOSTNAME_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


class SiteError(ValueError):
    pass


def subdomain_host(slug: str) -> str:
    return f"{slug}.{get_settings().sites_base_domain}".lower()


def get_site(db: TenantSession) -> Site | None:
    return db.scalar(select(Site).order_by(Site.created_at).limit(1))


def create_site(db: TenantSession, org_id: uuid.UUID, user_id: uuid.UUID | None, raw_profile: dict, slug: str) -> Site:
    profile = sitegen.validate_profile(raw_profile)
    slug = sitegen.validate_slug(slug)
    host = subdomain_host(slug)
    # Slug and hostname uniqueness are global; check without relying on RLS-visible rows.
    if db.scalar(select(SiteHost.hostname).where(SiteHost.hostname == host)):
        raise SiteError("That site address is already taken.")
    site = Site(org_id=org_id, slug=slug, draft=profile)
    db.add(site)
    db.flush()
    db.add(SiteHost(hostname=host, site_id=site.id, org_id=org_id, kind="subdomain", verified_at=now()))
    audit(db, org_id, user_id, "site.created", "site", site.id, {"slug": slug})
    return site


def update_draft(db: TenantSession, site: Site, user_id: uuid.UUID | None, raw_profile: dict) -> None:
    site.draft = sitegen.validate_profile(raw_profile)
    site.updated_at = now()
    audit(db, site.org_id, user_id, "site.draft_saved", "site", site.id)


def save_version(db: TenantSession, site: Site, user_id: uuid.UUID | None, note: str = "") -> SiteVersion:
    html, css = sitegen.render(site.draft)
    number = (db.scalar(select(func.max(SiteVersion.number)).where(SiteVersion.site_id == site.id)) or 0) + 1
    version = SiteVersion(org_id=site.org_id, site_id=site.id, number=number, content=site.draft,
                          html=html, css=css, note=note[:200], created_by=user_id)
    db.add(version)
    db.flush()
    audit(db, site.org_id, user_id, "site.version_saved", "site_version", version.id, {"number": number})
    return version


def approve_version(db: TenantSession, version: SiteVersion, user_id: uuid.UUID) -> None:
    if version.approved_at is None:
        version.approved_at = now()
        version.approved_by = user_id
        audit(db, version.org_id, user_id, "site.version_approved", "site_version", version.id, {"number": version.number})


def publish(db: TenantSession, site: Site, version: SiteVersion, user_id: uuid.UUID, *, rollback: bool = False) -> None:
    if version.site_id != site.id:
        raise SiteError("That version belongs to a different site.")
    if version.approved_at is None:
        raise SiteError("Approve this version before publishing it.")
    previous = site.published_version_id
    site.published_version_id = version.id
    audit(db, site.org_id, user_id, "site.rolled_back" if rollback else "site.published", "site_version",
          version.id, {"number": version.number, "previous_version_id": str(previous) if previous else None})


def unpublish(db: TenantSession, site: Site, user_id: uuid.UUID) -> None:
    site.published_version_id = None
    audit(db, site.org_id, user_id, "site.unpublished", "site", site.id)


def add_custom_domain(db: TenantSession, site: Site, user_id: uuid.UUID, hostname: str) -> SiteHost:
    hostname = (hostname or "").strip().lower().rstrip(".")
    settings = get_settings()
    if not HOSTNAME_RE.fullmatch(hostname):
        raise SiteError("Enter a domain like www.yourbusiness.com.")
    if hostname in settings.app_host_set or hostname.endswith("." + settings.sites_base_domain) \
            or hostname == settings.sites_base_domain:
        raise SiteError("That domain is reserved.")
    if db.scalar(select(SiteHost.hostname).where(SiteHost.hostname == hostname)):
        raise SiteError("That domain is already connected to a site.")
    # Unverified: traffic is not served on it until an operator confirms DNS + TLS.
    host = SiteHost(hostname=hostname, site_id=site.id, org_id=site.org_id, kind="custom", verified_at=None)
    db.add(host)
    audit(db, site.org_id, user_id, "site.domain_requested", "site", site.id, {"hostname": hostname})
    return host


def list_hosts(db: TenantSession, site: Site) -> list[SiteHost]:
    return list(db.scalars(select(SiteHost).where(SiteHost.site_id == site.id, SiteHost.org_id == site.org_id)
                           .order_by(SiteHost.created_at)))


# ----------------------------------------------------------- public (visitor) side

def resolve_host(db: TenantSession, hostname: str) -> SiteHost | None:
    """Map a request hostname to its owning site. Unverified custom domains do not resolve."""
    hostname = (hostname or "").split(":")[0].strip().lower().rstrip(".")
    if not hostname or len(hostname) > 253:
        return None
    row = db.get(SiteHost, hostname)
    if row is None or row.verified_at is None:
        return None
    return row


def published_version(db: TenantSession, host: SiteHost) -> SiteVersion | None:
    """Enter the owning tenant's context (derived from the hostname, never from the
    request body) and return its published snapshot."""
    db.set_context(host.org_id, None)
    site = db.get(Site, host.site_id)
    if site is None or site.published_version_id is None:
        return None
    return db.get(SiteVersion, site.published_version_id)


def record_event(db: TenantSession, host: SiteHost, kind: str) -> None:
    if kind not in ("page_view", "form_start", "lead"):
        return
    db.set_context(host.org_id, None)
    db.execute(text(
        "INSERT INTO site_events (id, org_id, site_id, day, kind, count) "
        "VALUES (:id, :org, :site, :day, :kind, 1) "
        "ON CONFLICT (site_id, day, kind) DO UPDATE SET count = site_events.count + 1"),
        {"id": uuid.uuid4(), "org": host.org_id, "site": host.site_id, "day": date.today(), "kind": kind})


def validate_lead(payload: dict, allowed_services: list[str]) -> dict | None:
    """Return a clean lead dict, or None when the honeypot was filled (silently dropped)."""
    if not isinstance(payload, dict):
        raise SiteError("Invalid request.")
    if payload.get("company_site"):
        return None
    row = {}
    for key, limit in (("name", 100), ("email", 120), ("phone", 40), ("message", 2000), ("service", 100), ("timing", 20)):
        value = payload.get(key, "")
        if not isinstance(value, str):
            raise SiteError("Invalid request.")
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()
        if len(value) > limit:
            raise SiteError("One of the fields is too long.")
        row[key] = value
    if not row["name"] or not row["message"]:
        raise SiteError("Please add your name and a short message.")
    if not sitegen.EMAIL_RE.fullmatch(row["email"]):
        raise SiteError("Please enter a valid email address.")
    if row["timing"] and row["timing"] not in sitegen.TIMINGS:
        raise SiteError("Invalid request.")
    if row["service"] and row["service"] not in allowed_services:
        row["service"] = ""
    return row


def capture_lead(db: TenantSession, host: SiteHost, clean: dict) -> Lead:
    db.set_context(host.org_id, None)
    lead = Lead(org_id=host.org_id, site_id=host.site_id, source="website", **clean)
    db.add(lead)
    db.flush()
    record_event(db, host, "lead")
    audit(db, host.org_id, None, "lead.received", "lead", lead.id, {"source": "website", "host": host.hostname})
    return lead


def measured_totals(db: TenantSession, site: Site | None, days: int = 30) -> dict:
    """First-party counters for the site. These are measurements, not estimates."""
    totals = {"page_view": 0, "form_start": 0, "lead": 0}
    if site is not None:
        rows = db.execute(text(
            "SELECT kind, COALESCE(SUM(count), 0) FROM site_events "
            "WHERE site_id = :s AND day >= CURRENT_DATE - :d GROUP BY kind"), {"s": site.id, "d": days}).all()
        for kind, count in rows:
            totals[kind] = int(count)
    return totals
