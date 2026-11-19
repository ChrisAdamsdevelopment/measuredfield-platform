"""MF-013: real FORCE-RLS, scope-restricted OAuth write and owner approval tests.

Everything uses synthetic businesses, no live OAuth provider, emails, or sends.
"""
from __future__ import annotations

import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.mcp_actions.drafts import OwnerDraftGateway
from app.mcp_connector.gateway import ConnectorAccessDenied, VerifiedOAuthIdentity
from app.mcp_plugin.tokens import VerifiedBearer
from app.models import (EmailMessage, Lead, McpActionDraft, McpIdentityLink,
                        Membership, User, now)

from .conftest import csrf_of, onboard_business

ISSUER = "https://auth.synthetic-oidc.test/"
AUDIENCE = "https://connector.synthetic-oidc.test/mcp"
CLIENT = "synthetic-chatgpt-oauth-client"
READ, WRITE = "mf:read", "mf:drafts.write"


def _owner(platform, label: str):
    email = label + "-" + uuid.uuid4().hex[:8] + "@fictional.test"
    client, org, _ = onboard_business(
        platform, business="Synthetic MCP " + label + " " + uuid.uuid4().hex[:6],
        owner_email=email)
    with session_scope(org_id=org) as db:
        lead = Lead(org_id=org, name="Fictional Client", email="customer@fictional.test",
                    phone="", service="Custom cabinet", timing="soon",
                    message="I need cabinets fitted, please call when convenient.",
                    source="website", status="new")
        db.add(lead)
        db.flush()
        lead_id = lead.id
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        db.add(McpIdentityLink(
            issuer=ISSUER, subject="oidc|" + uuid.uuid4().hex,
            user_id=user.id, verified_method="oidc_pkce_owner",
            linked_at=now() - timedelta(seconds=90)))
        db.flush()
        link = db.scalar(select(McpIdentityLink).where(McpIdentityLink.user_id == user.id))
        subject = link.subject
    return client, org, lead_id, email, user.id, subject


def _bearer(subject: str, *, scopes=frozenset({READ, WRITE}),
            issuer=ISSUER, audience=AUDIENCE, client_id=CLIENT,
            issued_at: int | None = None):
    return VerifiedBearer(
        identity=VerifiedOAuthIdentity(
            issuer=issuer, subject=subject, audience=audience,
            scopes=frozenset(scopes)),
        expires_at=int(time.time()) + 3600,
        issued_at=issued_at or int(time.time()),
        client_id=client_id,
    )


@pytest.fixture
def gw():
    return OwnerDraftGateway(issuer=ISSUER, audience=AUDIENCE,
                             read_scope=READ, write_scope=WRITE)


def _save(gw, subject, org, lead_id, *, body="Thanks for the inquiry. We can discuss your built-ins.",
          key="conversation-unique-0001", bearer=None):
    return gw.save_reply_draft(bearer or _bearer(subject), str(org), str(lead_id), body, key)


def test_signed_identity_with_write_scope_can_create_only_pending_draft(
        platform, gw):
    client, org, lead, email, user, subject = _owner(platform, "basic")
    result = _save(gw, subject, org, lead)
    assert result["status"] == "pending_review" and result["customer_contacted"] is False
    assert result["published"] is False and result["owner_review_required"] is True
    assert "/app/mcp/approvals" in result["review_url"]
    with session_scope(org_id=org, user_id=user) as db:
        draft = db.get(McpActionDraft, uuid.UUID(result["draft_id"]))
        assert draft.body.startswith("Thanks for the inquiry")
        assert draft.created_by == user and draft.lead_id == lead
        assert db.get(Lead, lead).status == "new"
        assert not db.scalars(select(EmailMessage).where(
            EmailMessage.kind == "lead_reply")).first()
    page = client.get("/app/mcp/approvals")
    assert page.status_code == 200
    assert "Fictional Client" in page.text and "Approve draft" in page.text
    assert "Thanks for the inquiry" in page.text


def test_replay_is_idempotent_and_key_cannot_be_reused_for_different_content(platform, gw):
    client, org, lead, email, user, subject = _owner(platform, "replay")
    first = _save(gw, subject, org, lead)
    second = _save(gw, subject, org, lead)
    assert first["draft_id"] == second["draft_id"]
    assert second["already_exists"] is True
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, subject, org, lead,
              body="Different suggestion inserted under the same request")
    with session_scope(org_id=org, user_id=user) as db:
        assert db.scalar(select(func.count()).select_from(McpActionDraft)) == 1


def test_missing_scope_foreign_business_and_restricted_roles_are_denied(platform, gw):
    alice, a_org, a_lead, a_email, a_user, a_subject = _owner(platform, "alice")
    bob, b_org, b_lead, b_email, b_user, b_subject = _owner(platform, "bob")
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, a_subject, a_org, a_lead,
              bearer=_bearer(a_subject, scopes={READ}))
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, a_subject, b_org, b_lead)
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, a_subject, a_org, b_lead)
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, a_subject, a_org, a_lead,
              bearer=_bearer(a_subject, issuer="https://forged.test/"))
    with session_scope(org_id=a_org, user_id=a_user) as db:
        member = db.scalar(select(Membership).where(
            Membership.user_id == a_user, Membership.org_id == a_org))
        member.role = "readonly"
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, a_subject, a_org, a_lead)
    assert alice.get("/app/mcp/approvals").status_code == 403
    with session_scope(org_id=a_org, user_id=a_user) as db:
        assert db.scalar(select(func.count()).select_from(McpActionDraft)) == 0
    with session_scope(org_id=b_org, user_id=b_user) as db:
        assert db.scalar(select(func.count()).select_from(McpActionDraft)) == 0


def test_revoked_oauth_link_and_disabled_user_stop_writes_immediately(platform, gw):
    client, org, lead, email, user, subject = _owner(platform, "revoked")
    _save(gw, subject, org, lead)
    with session_scope() as db:
        row = db.scalar(select(McpIdentityLink).where(
            McpIdentityLink.issuer == ISSUER, McpIdentityLink.subject == subject))
        row.revoked_at = now()
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, subject, org, lead, key="conversation-unique-0002")
    with session_scope() as db:
        row = db.scalar(select(McpIdentityLink).where(
            McpIdentityLink.issuer == ISSUER, McpIdentityLink.subject == subject))
        row.revoked_at = None
        db.get(User, user).disabled_at = now()
    with pytest.raises(ConnectorAccessDenied):
        _save(gw, subject, org, lead, key="conversation-unique-0003")
    with session_scope(org_id=org, user_id=user) as db:
        assert db.scalar(select(func.count()).select_from(McpActionDraft)) == 1


def test_owner_reviews_separately_with_csrf_and_no_email_send(platform, gw):
    owner, org, lead, email, user, subject = _owner(platform, "decision")
    created = _save(gw, subject, org, lead)
    path = "/app/mcp/approvals/" + created["draft_id"] + "/decide"
    missing = owner.post(path, data={"csrf": "bad", "decision": "approved", "confirm":"yes"})
    assert missing.status_code == 403
    wrong = owner.post(path, data={"csrf": csrf_of(owner),
                                   "decision": "approved", "confirm": "no"})
    assert wrong.status_code == 422
    with session_scope(org_id=org, user_id=user) as db:
        draft = db.get(McpActionDraft, uuid.UUID(created["draft_id"]))
        assert draft.status == "pending_review"
    ok = owner.post(path, data={"csrf": csrf_of(owner),
                                "decision":"approved", "confirm":"yes"})
    assert ok.status_code == 303
    with session_scope(org_id=org, user_id=user) as db:
        draft = db.get(McpActionDraft, uuid.UUID(created["draft_id"]))
        assert draft.status == "approved" and draft.reviewed_by == user
        assert draft.reviewed_at is not None
        assert db.get(Lead, lead).status == "new"
        assert not db.scalar(select(EmailMessage.id).where(
            EmailMessage.kind == "lead_reply"))
    assert owner.post(path, data={"csrf":csrf_of(owner),
                                  "decision":"approved", "confirm":"yes"}).status_code == 303
    assert owner.post(path, data={"csrf":csrf_of(owner),
                                  "decision":"declined", "confirm":"yes"}).status_code == 409


def test_other_owner_cannot_approve_foreign_draft_with_forged_id(platform, gw):
    owner_a, org_a, lead_a, email_a, user_a, subject_a = _owner(platform, "review_a")
    owner_b, org_b, lead_b, email_b, user_b, subject_b = _owner(platform, "review_b")
    draft = _save(gw, subject_a, org_a, lead_a)
    wrong = owner_b.post("/app/mcp/approvals/" + draft["draft_id"] + "/decide",
                         data={"csrf": csrf_of(owner_b), "decision":"approved","confirm":"yes"})
    assert wrong.status_code == 404
    with session_scope(org_id=org_b, user_id=user_b) as db:
        assert db.get(McpActionDraft, uuid.UUID(draft["draft_id"])) is None
    with session_scope(org_id=org_a, user_id=user_a) as db:
        assert db.get(McpActionDraft, uuid.UUID(draft["draft_id"])).status == "pending_review"


def test_race_guard_and_draft_table_is_force_rls(platform, gw):
    client, org, lead, email, user, subject = _owner(platform, "isolation")
    new_draft = _save(gw, subject, org, lead)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(McpActionDraft)) == 0
    with session_scope(org_id=org, user_id=user) as db:
        assert db.scalar(select(func.count()).select_from(McpActionDraft)) == 1
        row = db.get(McpActionDraft, uuid.UUID(new_draft["draft_id"]))
        assert row.oauth_client_id == CLIENT


@pytest.mark.parametrize("invisible", ["\u202e", "\u200b", "\u2066", "\ufeff", "\u061c"])
def test_invisible_or_bidi_controls_cannot_be_submitted_as_approval_text(
        platform, gw, invisible):
    client, org, lead, email, user, subject = _owner(platform, "unicode")
    with pytest.raises(ValueError, match="invisible or bidirectional"):
        _save(gw, subject, org, lead, body="Hello " + invisible + "customer")
    with session_scope(org_id=org, user_id=user) as db:
        assert db.scalar(select(func.count()).select_from(McpActionDraft)) == 0
