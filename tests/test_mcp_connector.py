"""MF-012 read-only gateway: real PostgreSQL RLS, membership and identity tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.mcp_connector.gateway import (
    ConnectorAccessDenied,
    ReadOnlyBusinessGateway,
    VerifiedOAuthIdentity,
)
from app.models import Lead, Membership, User, now
from .conftest import onboard_business

ISSUER = "https://idp.synthetic.invalid"
AUDIENCE = "https://mcp.synthetic.invalid/mcp"


def identity(
    subject: str = "external-a",
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    scopes: frozenset[str] = frozenset({"mf:read"}),
):
    return VerifiedOAuthIdentity(issuer, subject, audience, scopes)


def test_no_implicit_external_subject_mapping():
    gateway = ReadOnlyBusinessGateway(issuer=ISSUER, resource_audience=AUDIENCE)
    with pytest.raises(ConnectorAccessDenied):
        gateway.businesses_for(identity())
    with pytest.raises(ConnectorAccessDenied):
        gateway.businesses_for(identity(scopes=frozenset()))
    with pytest.raises(ConnectorAccessDenied):
        gateway.businesses_for(identity(issuer="https://other.invalid"))


@pytest.fixture
def two_businesses(platform):
    # Every case uses a fresh synthetic identity; the PostgreSQL fixture is session-scoped.
    suffix = uuid.uuid4().hex[:12]
    email_a = f"owner-a-{suffix}@invalid.test"
    email_b = f"owner-b-{suffix}@invalid.test"
    onboard_business(
        platform, business="Example A Landscaping", owner_email=email_a
    )
    onboard_business(
        platform, business="Example B Cleaning", owner_email=email_b
    )
    with session_scope() as db:
        a = db.scalar(select(User).where(User.email == email_a))
        b = db.scalar(select(User).where(User.email == email_b))
        assert a and b
        a_id, b_id = a.id, b.id
    with session_scope(user_id=a_id) as db:
        a_org = db.scalar(select(Membership.org_id).where(Membership.user_id == a_id))
    with session_scope(user_id=b_id) as db:
        b_org = db.scalar(select(Membership.org_id).where(Membership.user_id == b_id))
    assert a_org and b_org and a_org != b_org
    linked = {(ISSUER, "external-a"): a_id, (ISSUER, "external-b"): b_id}
    gateway = ReadOnlyBusinessGateway(
        issuer=ISSUER,
        resource_audience=AUDIENCE,
        resolve_subject=lambda iss, sub: linked.get((iss, sub)),
    )
    return gateway, a_id, b_id, a_org, b_org


def test_tenant_bound_business_listing_and_cross_tenant_denial(two_businesses):
    gateway, _, _, a_org, b_org = two_businesses
    mine = gateway.businesses_for(identity())
    assert [x["id"] for x in mine] == [str(a_org)]
    assert mine[0]["name"] == "Example A Landscaping"
    assert gateway.businesses_for(identity("external-b"))[0]["id"] == str(b_org)
    with pytest.raises(ConnectorAccessDenied, match="Business is unavailable"):
        gateway.overview_for(identity(), str(b_org))
    with pytest.raises(ConnectorAccessDenied):
        gateway.inquiries_for(identity(), str(b_org))


def test_inquiry_detail_and_derived_metrics_never_cross_business(two_businesses):
    gateway, a_id, b_id, a_org, b_org = two_businesses
    with session_scope(org_id=a_org, user_id=a_id) as db:
        row_a = Lead(
            org_id=a_org,
            name="Synthetic A Caller",
            email="a@example.invalid",
            message="Need a landscape estimate",
            status="new",
        )
        db.add(row_a)
        db.flush()
        lead_a_id = row_a.id
    with session_scope(org_id=b_org, user_id=b_id) as db:
        row_b = Lead(
            org_id=b_org,
            name="Synthetic B Caller",
            email="b@example.invalid",
            message="Need a cleaning quote",
            status="new",
        )
        db.add(row_b)
        db.flush()
        lead_b_id = row_b.id

    mine = gateway.inquiries_for(identity(), str(a_org), limit=10)
    assert [x["id"] for x in mine] == [str(lead_a_id)]
    detail = gateway.inquiry_for(identity(), str(a_org), str(lead_a_id))
    assert detail["name"] == "Synthetic A Caller"
    assert detail["evidence"] == "measuredfield_first_party_inquiry_record"
    overview = gateway.overview_for(identity(), str(a_org))
    assert overview["inquiries_total"] == 1 and overview["inquiries_new"] == 1
    assert (
        overview["search_rankings"] is None and overview["revenue_attributed"] is None
    )
    with pytest.raises(ConnectorAccessDenied, match="Inquiry is unavailable"):
        gateway.inquiry_for(identity(), str(a_org), str(lead_b_id))
    with pytest.raises(ConnectorAccessDenied):
        gateway.inquiry_for(identity(), str(b_org), str(lead_b_id))


def test_membership_revocation_disables_access_immediately(two_businesses):
    gateway, a_id, _, a_org, _ = two_businesses
    assert gateway.businesses_for(identity())
    with session_scope(org_id=a_org, user_id=a_id) as db:
        membership = db.scalar(
            select(Membership).where(
                Membership.user_id == a_id, Membership.org_id == a_org
            )
        )
        db.delete(membership)
    assert gateway.businesses_for(identity()) == []
    with pytest.raises(ConnectorAccessDenied):
        gateway.overview_for(identity(), str(a_org))


def test_disabled_user_and_bad_claims_are_denied(two_businesses):
    gateway, a_id, _, a_org, _ = two_businesses
    with pytest.raises(ConnectorAccessDenied):
        gateway.overview_for(identity(scopes=frozenset()), str(a_org))
    with pytest.raises(ConnectorAccessDenied):
        gateway.overview_for(identity(audience="https://wrong.invalid/mcp"), str(a_org))
    with pytest.raises(ConnectorAccessDenied):
        gateway.overview_for(identity("not-linked"), str(a_org))
    with pytest.raises(ValueError):
        gateway.inquiries_for(identity(), str(a_org), limit=True)
    with session_scope() as db:
        user = db.get(User, a_id)
        user.disabled_at = now()
    with pytest.raises(ConnectorAccessDenied, match="Account is unavailable"):
        gateway.inquiries_for(identity(), str(a_org))


def test_suspending_organization_denies_all_tools(two_businesses):
    gateway, a_id, _, a_org, _ = two_businesses
    from app.models import Organization

    with session_scope(org_id=a_org, user_id=a_id) as db:
        db.get(Organization, a_org).status = "deletion_pending"
    assert gateway.businesses_for(identity()) == []
    with pytest.raises(ConnectorAccessDenied):
        gateway.inquiry_for(identity(), str(a_org), str(uuid.uuid4()))
