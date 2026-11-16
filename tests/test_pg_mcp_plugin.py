"""MF-037 PostgreSQL checks: real issuer/sub links, revocation, account isolation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.mcp_connector.gateway import (
    ConnectorAccessDenied, ReadOnlyBusinessGateway, VerifiedOAuthIdentity,
)
from app.mcp_plugin.links import linked_subject_user_id
from .conftest import onboard_business

ISSUER = "https://issuer.synthetic-identity.example"
RESOURCE = "https://mcp.synthetic-identity.example/mcp"


def _owner(platform, suffix):
    from app.db import session_scope
    from app.models import Membership
    owner, org_id, _ = onboard_business(
        platform, business="Linked Business " + suffix,
        owner_email="mcp-linked-" + suffix + "@fictional.test")
    with session_scope(org_id=org_id) as db:
        membership = db.scalar(select(Membership).where(
            Membership.org_id == org_id,
            Membership.role == "owner"))
        assert membership is not None
        user_id = membership.user_id
    return org_id, user_id


def _link(user_id, subject):
    from app.db import session_scope
    from app.models import McpIdentityLink
    with session_scope() as db:
        item = McpIdentityLink(
            issuer=ISSUER, subject=subject, user_id=user_id,
            verified_method="oidc_pkce_owner")
        db.add(item)
        db.flush()
        return item.id, int(item.linked_at.timestamp())


def _gateway():
    return ReadOnlyBusinessGateway(
        issuer=ISSUER, resource_audience=RESOURCE,
        resolve_subject=lambda issuer, sub: linked_subject_user_id(issuer, sub))


def _identity(sub):
    return VerifiedOAuthIdentity(
        issuer=ISSUER, subject=sub, audience=RESOURCE,
        scopes=frozenset({"mf:read"}))


def test_unlinked_and_wrong_issuer_cannot_impersonate_owner(platform):
    org, user = _owner(platform, "no-link")
    assert linked_subject_user_id(ISSUER, "unlinked") is None
    with pytest.raises(ConnectorAccessDenied):
        _gateway().overview_for(_identity("unlinked"), str(org))
    _link(user, "oidc|verified-user-1")
    assert linked_subject_user_id("https://wrong.example", "oidc|verified-user-1") is None
    assert linked_subject_user_id(ISSUER, "oidc|verified-user-1") == user


def test_link_allows_only_current_business_membership_and_revokes_immediately(platform):
    from app.db import session_scope
    from app.models import McpIdentityLink
    org_a, user_a = _owner(platform, "alpha")
    org_b, user_b = _owner(platform, "beta")
    link_id, linked_epoch = _link(user_a, "oidc|alpha-owner")
    assert user_a != user_b
    assert linked_subject_user_id(ISSUER, "oidc|alpha-owner", linked_epoch) == user_a
    assert linked_subject_user_id(ISSUER, "oidc|alpha-owner", linked_epoch - 100) is None

    gateway = _gateway()
    profile = _identity("oidc|alpha-owner")
    businesses = gateway.businesses_for(profile)
    ids = {record["id"] for record in businesses}
    assert str(org_a) in ids and str(org_b) not in ids
    overview = gateway.overview_for(profile, str(org_a))
    assert overview["business_id"] == str(org_a)
    with pytest.raises(ConnectorAccessDenied):
        gateway.overview_for(profile, str(org_b))
    with session_scope() as db:
        row = db.get(McpIdentityLink, link_id)
        row.revoked_at = datetime.now(timezone.utc)
    assert linked_subject_user_id(ISSUER, "oidc|alpha-owner") is None
    with pytest.raises(ConnectorAccessDenied):
        gateway.businesses_for(profile)


def test_disabled_local_account_invalidates_oauth_link(platform):
    from app.db import session_scope
    from app.models import User
    org, user = _owner(platform, "disabled")
    _link(user, "oidc|disabled")
    assert linked_subject_user_id(ISSUER, "oidc|disabled") == user
    with session_scope() as db:
        db.get(User, user).disabled_at = datetime.now(timezone.utc)
    assert linked_subject_user_id(ISSUER, "oidc|disabled") is None
    with pytest.raises(ConnectorAccessDenied):
        _gateway().businesses_for(_identity("oidc|disabled"))


def test_unique_identity_cannot_be_claimed_by_second_user(platform):
    from app.db import session_scope
    from app.models import McpIdentityLink
    _, first = _owner(platform, "first-claim")
    _, second = _owner(platform, "second-claim")
    _link(first, "oidc|already-bound")
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(McpIdentityLink(
                issuer=ISSUER, subject="oidc|already-bound", user_id=second,
                verified_method="oidc_pkce_owner"))
            db.flush()
    assert linked_subject_user_id(ISSUER, "oidc|already-bound") == first


def test_unverified_binding_method_cannot_be_inserted(platform):
    from app.db import session_scope
    from app.models import McpIdentityLink
    _, user = _owner(platform, "no-unverified")
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(McpIdentityLink(
                issuer=ISSUER, subject="oidc|not-verified", user_id=user,
                verified_method="user_supplied_text"))
            db.flush()
    assert linked_subject_user_id(ISSUER, "oidc|not-verified") is None


def test_mcp_link_is_platform_only_and_no_tenant_content_crosses_boundary(platform):
    from app.db import get_engine
    from sqlalchemy import text
    _, user = _owner(platform, "platform-link")
    _link(user, "oidc|platform-link")
    with get_engine().connect() as conn:
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='mcp_identity_links'"))}
        assert "org_id" not in cols
        assert {"issuer", "subject", "user_id", "verified_method", "revoked_at"} <= cols
