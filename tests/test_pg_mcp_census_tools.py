"""MF-075: real PostgreSQL owner/tenant gates; synthetic Census transport only."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.mcp_connector.gateway import ConnectorAccessDenied, VerifiedOAuthIdentity
from app.mcp_plugin import census_market_tools as census_tools
from app.models import McpIdentityLink, Membership
from app.routers import census_market as market
from app.services.census_market import CensusMarketClient

from .test_census_market import fake_matrix
from .test_pg_mcp_plugin import ISSUER, RESOURCE, _gateway, _identity, _link, _owner

FAKE_KEY = "A" * 40


@pytest.fixture
def verified_owner(platform, monkeypatch):
    # The PostgreSQL platform fixture is shared across test cases; each case
    # needs distinct signup emails and OIDC subjects.
    suffix = uuid4().hex[:12]
    org_a, user_a = _owner(platform, "census-a-" + suffix)
    org_b, _ = _owner(platform, "census-b-" + suffix)
    link_id, _ = _link(user_a, "oidc|pg-census-owner-" + suffix)
    monkeypatch.setattr(market, "_CENSUS", CensusMarketClient(
        transport=fake_matrix, key_loader=lambda: FAKE_KEY, max_calls=50))
    return (org_a, org_b, user_a, link_id, _gateway(),
            _identity("oidc|pg-census-owner-" + suffix))


def test_authorized_owner_gets_public_source_vintages(verified_owner):
    own, other, _user, _link_id, gateway, identity = verified_owner
    choices = census_tools.available_markets(
        identity=identity, business_id=str(own), gateway=gateway)
    assert {v["id"] for v in choices["markets"]} == set(market.AREAS)
    result = census_tools.market_snapshot(
        identity=identity, business_id=str(own),
        area_slug="prescott-valley", gateway=gateway)
    assert result["business_id"] == str(own)
    assert len(result["source_records"]) == 6
    assert {r["reference_year"] for r in result["source_records"]} == {2023, 2024}
    assert all(r["data_class"] == "official_public_aggregate"
               for r in result["source_records"])
    assert str(other) not in str(result)
    assert FAKE_KEY not in str(result)


def test_wrong_tenant_cannot_call_or_consume_provider(verified_owner, monkeypatch):
    _own, other, _user, _link_id, gateway, identity = verified_owner
    monkeypatch.setattr(market, "_snapshot", lambda *_:
                        pytest.fail("Cross-tenant data request reached Census"))
    with pytest.raises(ConnectorAccessDenied):
        census_tools.available_markets(
            identity=identity, business_id=str(other), gateway=gateway)
    with pytest.raises(ConnectorAccessDenied):
        census_tools.market_snapshot(
            identity=identity, business_id=str(other),
            area_slug="prescott", gateway=gateway)


def test_unlinked_or_wrong_scope_never_reaches_provider(verified_owner, monkeypatch):
    own, _other, _user, _link_id, gateway, identity = verified_owner
    monkeypatch.setattr(market, "_snapshot", lambda *_:
                        pytest.fail("Unlinked owner reached Census"))
    profiles = (
        _identity("oidc|not-linked"),
        VerifiedOAuthIdentity(issuer=ISSUER, subject=identity.subject,
                              audience=RESOURCE, scopes=frozenset()),
        VerifiedOAuthIdentity(issuer="https://different.invalid",
                              subject=identity.subject, audience=RESOURCE,
                              scopes=frozenset({"mf:read"})),
    )
    for bad in profiles:
        with pytest.raises(ConnectorAccessDenied):
            census_tools.market_snapshot(
                identity=bad, business_id=str(own),
                area_slug="prescott", gateway=gateway)


def test_revoked_owner_and_removed_membership_denied(verified_owner, monkeypatch):
    own, _other, user, link_id, gateway, identity = verified_owner
    monkeypatch.setattr(market, "_snapshot", lambda *_:
                        pytest.fail("Revoked link or membership reached Census"))
    with session_scope(org_id=own) as db:
        membership = db.scalar(select(Membership).where(
            Membership.org_id == own, Membership.user_id == user))
        assert membership
        db.delete(membership)
    with pytest.raises(ConnectorAccessDenied):
        census_tools.market_snapshot(
            identity=identity, business_id=str(own),
            area_slug="prescott", gateway=gateway)

    # Restore no membership: revocation must independently remain denied.
    with session_scope() as db:
        row = db.get(McpIdentityLink, link_id)
        row.revoked_at = datetime.now(timezone.utc)
    with pytest.raises(ConnectorAccessDenied):
        census_tools.available_markets(
            identity=identity, business_id=str(own), gateway=gateway)


def test_hourly_quota_prevents_retry_by_changing_market_id(
    verified_owner, monkeypatch,
):
    own, _other, _user, _link_id, gateway, identity = verified_owner
    requested = []
    monkeypatch.setattr(market, "_snapshot",
                        lambda area: requested.append(area.slug) or
                        ({"records": {}}, ()))
    for _ in range(15):
        census_tools.market_snapshot(
            identity=identity, business_id=str(own),
            area_slug="prescott", gateway=gateway)
    assert len(requested) == 15
    with pytest.raises(ConnectorAccessDenied, match="limit"):
        census_tools.market_snapshot(
            identity=identity, business_id=str(own),
            area_slug="chino-valley", gateway=gateway)
    assert len(requested) == 15
