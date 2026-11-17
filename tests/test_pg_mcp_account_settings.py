"""MF-040: real PostgreSQL owner-controlled MCP OAuth link visibility/revocation."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.mcp_connector.gateway import (
    ConnectorAccessDenied, ReadOnlyBusinessGateway, VerifiedOAuthIdentity
)
from app.mcp_plugin.links import linked_subject_user_id
from app.models import McpIdentityLink, Membership
from .conftest import client_for, csrf_of, onboard_business

ISSUER = "https://idp.synthetic-mcp.example"
RESOURCE = "https://connector.synthetic-mcp.example/mcp"


def _owner(platform, suffix):
    client, org_id, _ = onboard_business(
        platform, business="Private ChatGPT " + suffix,
        owner_email="chatgpt-" + suffix + "@fictional.example")
    with session_scope(org_id=org_id) as db:
        membership = db.scalar(select(Membership).where(
            Membership.org_id == org_id,
            Membership.role == "owner"))
        assert membership is not None
        user_id = membership.user_id
    return client, org_id, user_id


def _link(user_id, subject):
    with session_scope() as db:
        item = McpIdentityLink(
            issuer=ISSUER, subject=subject, user_id=user_id,
            verified_method="oidc_pkce_owner")
        db.add(item)
        db.flush()
        return str(item.id)


def test_owner_connection_page_lists_only_personal_connections(platform):
    a, a_org, a_user = _owner(platform, "page-a")
    b, b_org, b_user = _owner(platform, "page-b")
    first = _link(a_user, "oidc|private-first-subject")
    second = _link(b_user, "oidc|private-other-subject")

    resp = a.get("/app/connections")
    assert resp.status_code == 200
    assert "ChatGPT connections" in resp.text
    assert "Disconnect this account" in resp.text
    assert first in resp.text
    assert second not in resp.text
    assert "oidc|private" not in resp.text
    assert "ChatGPT Connections" in a.get("/app").text

    data = a.get("/api/v1/mcp/account-links")
    assert data.status_code == 200
    links = data.json()["connections"]
    assert {i["id"] for i in links} == {first}
    assert links[0]["provider"] == "idp.synthetic-mcp.example"
    assert links[0]["status"] == "connected"
    assert "issuer" not in links[0] and "subject" not in links[0]
    assert "email" not in links[0] and "user_id" not in links[0]

    b_links = b.get("/api/v1/mcp/account-links").json()["connections"]
    assert {link["id"] for link in b_links} == {second}


def test_unauthenticated_session_cannot_list_or_disconnect_links(platform):
    visitor = client_for(platform)
    assert visitor.get("/api/v1/mcp/account-links").status_code == 401
    assert visitor.get("/app/connections").status_code == 303
    assert visitor.post("/api/v1/mcp/account-links/00000000-0000-0000-0000-000000000000/disconnect",
                        json={"confirm": "DISCONNECT CHATGPT"}).status_code == 401


def test_csrf_and_explicit_confirmation_block_account_disconnect(platform):
    owner, _, user = _owner(platform, "csrf")
    link_id = _link(user, "oidc|csrf-owner")
    url = f"/api/v1/mcp/account-links/{link_id}/disconnect"
    assert owner.post(url, json={"confirm": "DISCONNECT CHATGPT"}).status_code == 403
    csrf = {"X-CSRF-Token": csrf_of(owner)}
    assert owner.post(url, json={"confirm": "not approved"}, headers=csrf).status_code == 422
    assert owner.post(url, json={"confirm": "DISCONNECT CHATGPT"},
                      headers={"X-CSRF-Token": "0" * 64}).status_code == 403
    form = f"/app/connections/{link_id}/disconnect"
    assert owner.post(form, data={"confirm": "DISCONNECT"}).status_code == 403
    refused = owner.post(form, data={"csrf": csrf_of(owner), "confirm": "no"})
    assert refused.status_code == 303
    assert linked_subject_user_id(ISSUER, "oidc|csrf-owner") == user


def test_owner_disconnect_invalidates_real_token_subject_mapping(platform):
    owner, org_id, user = _owner(platform, "immediate")
    subject = "oidc|immediate-owner"
    link_id = _link(user, subject)
    identity = VerifiedOAuthIdentity(
        issuer=ISSUER, subject=subject, audience=RESOURCE,
        scopes=frozenset({"mf:read"}))
    gateway = ReadOnlyBusinessGateway(
        issuer=ISSUER, resource_audience=RESOURCE,
        resolve_subject=lambda issuer, sub: linked_subject_user_id(issuer, sub))
    assert gateway.overview_for(identity, str(org_id))["business_id"] == str(org_id)
    url = f"/api/v1/mcp/account-links/{link_id}/disconnect"
    csrf = {"X-CSRF-Token": csrf_of(owner)}
    first = owner.post(url, json={"confirm": "DISCONNECT CHATGPT"}, headers=csrf)
    assert first.status_code == 200, first.text[:200]
    assert first.json()["disconnected_now"] is True
    assert first.json()["connection"]["status"] == "disconnected"
    assert linked_subject_user_id(ISSUER, subject) is None
    with pytest.raises(ConnectorAccessDenied):
        gateway.overview_for(identity, str(org_id))
    second = owner.post(url, json={"confirm": "DISCONNECT CHATGPT"}, headers=csrf)
    assert second.status_code == 200 and second.json()["disconnected_now"] is False
    assert owner.get("/api/v1/mcp/account-links").json()["connections"][0]["status"] == "disconnected"


def test_cannot_revoke_another_users_link_or_guess_identifier(platform):
    a, _, a_user = _owner(platform, "idor-a")
    b, _, b_user = _owner(platform, "idor-b")
    link_a = _link(a_user, "oidc|scope-a")
    link_b = _link(b_user, "oidc|scope-b")
    token = {"X-CSRF-Token": csrf_of(a)}
    for other in (link_b, "00000000-0000-0000-0000-000000000000", "not-a-uuid"):
        response = a.post(
            f"/api/v1/mcp/account-links/{other}/disconnect",
            json={"confirm": "DISCONNECT CHATGPT"},
            headers=token,
        )
        assert response.status_code == 404
    assert linked_subject_user_id(ISSUER, "oidc|scope-b") == b_user
    assert linked_subject_user_id(ISSUER, "oidc|scope-a") == a_user
    assert link_a != link_b


def test_browser_form_disconnect_and_repeated_clicks_are_safe(platform):
    owner, _, user = _owner(platform, "browser")
    link_id = _link(user, "oidc|browser")
    url = f"/app/connections/{link_id}/disconnect"
    first = owner.post(url, data={"csrf": csrf_of(owner), "confirm": "DISCONNECT"})
    assert first.status_code == 303
    assert linked_subject_user_id(ISSUER, "oidc|browser") is None
    page = owner.get("/app/connections")
    assert page.status_code == 200
    assert "Disconnected" in page.text
    assert "Disconnect this account" not in page.text
    repeated = owner.post(url, data={"csrf": csrf_of(owner), "confirm": "DISCONNECT"})
    assert repeated.status_code == 303
