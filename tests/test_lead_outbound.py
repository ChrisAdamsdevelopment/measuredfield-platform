"""MF-004: no-live-email tenant/CSRF/approval/idempotent send regression."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.models import Job, Lead, LeadReply

from .conftest import csrf_of, onboard_business


def _case(platform, suffix: str):
    owner, org, _ = onboard_business(
        platform, business="Fictional Reply " + suffix + " " + uuid.uuid4().hex[:6],
        owner_email=f"reply-{suffix}-{uuid.uuid4().hex[:6]}@fictional.test")
    with session_scope(org_id=org) as db:
        lead = Lead(org_id=org, name="Fictional Customer",
                    email="inquirer@fictional.test", service="Remodeling",
                    message="Could you provide an estimate?", status="new")
        db.add(lead)
        db.flush()
        lead_id = lead.id
    return owner, org, lead_id


def _draft(owner, lead_id, csrf=None, *, subject="Estimate next steps",
           body="We can arrange a short meeting to discuss your project."):
    return owner.post(f"/app/inquiries/{lead_id}/reply/draft",
                      data={"csrf": csrf or csrf_of(owner), "subject": subject, "body": body})


def test_draft_is_inert_missing_provider_preserves_approval_gate(platform):
    owner, org, lead_id = _case(platform, "draft")
    page = owner.get(f"/app/inquiries/{lead_id}")
    assert page.status_code == 200 and "Reply to this inquiry" in page.text
    csrf = csrf_of(owner)
    assert owner.post(f"/app/inquiries/{lead_id}/reply/draft",
                      data={"csrf": "forged", "subject": "Wrong", "body": "Wrong"}).status_code == 403
    assert _draft(owner, lead_id, csrf).status_code == 303
    with session_scope(org_id=org) as db:
        reply = db.scalars(select(LeadReply).where(LeadReply.lead_id == lead_id)).one()
        reply_id = reply.id
        assert reply.status == "draft" and reply.approved_by is None
        assert db.get(Lead, lead_id).first_response_at is None
    assert owner.post(f"/app/inquiries/{lead_id}/reply/{reply_id}/approve",
                      data={"csrf": "forged"}).status_code == 403
    assert owner.post(f"/app/inquiries/{lead_id}/reply/{reply_id}/approve",
                      data={"csrf": csrf}).status_code == 303
    with session_scope(org_id=org) as db:
        reply = db.get(LeadReply, reply_id)
        assert reply.status == "draft"
        assert db.scalar(select(func.count()).select_from(Job).where(
            Job.idempotency_key == f"lead_reply:{reply_id}")) == 0


def test_owner_approves_exact_reply_provider_acceptance_is_not_delivery(platform, monkeypatch):
    from app.routers import workspace
    from app.services import email, jobs

    owner, org, lead_id = _case(platform, "approve")
    csrf = csrf_of(owner)
    assert _draft(owner, lead_id, csrf).status_code == 303
    with session_scope(org_id=org) as db:
        reply_id = db.scalars(select(LeadReply).where(LeadReply.lead_id == lead_id)).one().id

    monkeypatch.setattr(workspace, "get_settings", lambda: SimpleNamespace(
        email_provider="resend", resend_api_key="synthetic-only",
        email_from="sender@fictional.test"))
    calls = []

    class FakeResend:
        name = "resend"
        def send(self, *, to, subject, body, idempotency_key):
            calls.append((to, subject, body, idempotency_key))
            return email.SendResult(True, provider_message_id="synthetic-provider-accepted")

    monkeypatch.setattr(email, "get_provider", lambda: FakeResend())
    path = f"/app/inquiries/{lead_id}/reply/{reply_id}/approve"
    assert owner.post(path, data={"csrf": csrf}).status_code == 303
    assert owner.post(path, data={"csrf": csrf}).status_code == 303
    planned = SimpleNamespace(org_id=org, payload={"reply_id": str(reply_id)})
    jobs._send_lead_reply(planned)
    jobs._send_lead_reply(planned)
    with session_scope(org_id=org) as db:
        reply = db.get(LeadReply, reply_id)
        assert reply.status == "accepted" and reply.provider_message_id == "synthetic-provider-accepted"
        assert reply.approved_by is not None and reply.accepted_at is not None
        lead = db.get(Lead, lead_id)
        assert lead.status == "contacted" and lead.first_response_at is not None
        assert db.scalar(select(func.count()).select_from(Job).where(
            Job.idempotency_key == f"lead_reply:{reply_id}")) == 1
    assert len(calls) == 1
    assert calls[0][3] == f"lead-reply/{reply_id}"
    assert "delivery" in owner.get(f"/app/inquiries/{lead_id}").text.lower()


def test_cross_tenant_inquiry_and_reply_are_invisible(platform):
    owner_a, org_a, lead_a = _case(platform, "alpha")
    owner_b, org_b, _ = _case(platform, "beta")
    csrf_b = csrf_of(owner_b)
    assert owner_b.get(f"/app/inquiries/{lead_a}").status_code == 404
    assert _draft(owner_b, lead_a, csrf_b).status_code == 404
    with session_scope(org_id=org_a) as db:
        assert db.scalar(select(func.count()).select_from(LeadReply)) == 0
    with session_scope(org_id=org_b) as db:
        assert db.scalar(select(func.count()).select_from(LeadReply)) == 0


def test_provider_failure_has_no_false_response_timestamp(platform, monkeypatch):
    from app.services import email, jobs

    owner, org, lead_id = _case(platform, "failed")
    assert _draft(owner, lead_id).status_code == 303
    with session_scope(org_id=org) as db:
        reply = db.scalars(select(LeadReply).where(LeadReply.lead_id == lead_id)).one()
        reply.approved_by = reply.created_by
        from app.models import now
        reply.approved_at, reply.status = now(), "queued"
        reply_id = reply.id

    class DeniedResend:
        name = "resend"
        def send(self, **kwargs):
            return email.SendResult(False, error="PRIVATE CUSTOMER DATA MUST NOT APPEAR")

    monkeypatch.setattr(email, "get_provider", lambda: DeniedResend())
    with pytest.raises(RuntimeError, match="lead_reply_provider_failure"):
        jobs._send_lead_reply(SimpleNamespace(org_id=org, payload={"reply_id": str(reply_id)}))
    with session_scope(org_id=org) as db:
        stored = db.get(LeadReply, reply_id)
        assert stored.status == "failed"
        assert stored.last_error == "provider_not_accepted"
        assert db.get(Lead, lead_id).first_response_at is None


def test_resend_lead_reply_sets_provider_idempotency_header_without_network():
    from app.services.email import ResendProvider

    received = {}
    def sender(request):
        received["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(200, json={"id": "synthetic-accepted"})
    client = httpx.Client(transport=httpx.MockTransport(sender))
    provider = ResendProvider("synthetic-key", "sender@fictional.test", client=client)
    result = provider.send(to="inquirer@fictional.test", subject="Hello",
                           body="Synthetic", idempotency_key="lead-reply/synthetic")
    assert result.ok and received["key"] == "lead-reply/synthetic"
    client.close()
