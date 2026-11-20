"""MF-085: real restricted-PostgreSQL self-serve beta and mail-token isolation."""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import AuthToken, EmailMessage, Organization, PendingPaymentLinkClaim, ServiceOrder, now
from app.services.email import AUTH_LINK_PLACEHOLDER, SendResult

from .conftest import client_for


class FakeResend:
    name = "resend"

    def __init__(self):
        self.messages = []

    def send(self, *, to: str, subject: str, body: str):
        self.messages.append((to, subject, body))
        return SendResult(True, provider_message_id="synthetic-provider-" + str(len(self.messages)))


@pytest.fixture
def mail_ready(platform, monkeypatch):
    fake = FakeResend()
    monkeypatch.setenv("MF_EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("MF_RESEND_API_KEY", "re_synthetic_not_a_real_key")
    monkeypatch.setenv("MF_EMAIL_FROM", "MeasuredField <notifications@testserver>")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.email.get_provider", lambda settings=None: fake)
    monkeypatch.setattr("app.routers.beta_signup.get_provider", lambda settings=None: fake)
    yield fake
    get_settings.cache_clear()


def _verified_payment_claim(email):
    """Synthetic signed-Stripe evidence, inserted directly in disposable test DB."""
    from app.services.payment_link_claims import LINKS
    link = "plink_1UcxSA2LWll6Mkhu7MR1P3dr"
    marker = uuid.uuid4().hex
    with session_scope() as db:
        db.add(PendingPaymentLinkClaim(
            checkout_session_id="cs_test_" + marker,
            stripe_subscription_id="sub_test_" + marker,
            stripe_customer_id="cus_test_" + marker,
            stripe_payment_link_id=link, stripe_price_id=LINKS[link].price_id,
            offer_code="operate", billing_email=email,
            amount_subtotal_cents=9900, amount_total_cents=0,
            discount_cents=9900, tax_cents=0,
            stripe_event_id="evt_test_" + marker,
            subscription_status="active",
            period_end=now() + timedelta(days=30),
            status="pending_claim", verified_at=now(),
        ))


def _signup(client, *, name, email, industry="professional", consent="yes", **other):
    fields = {
        "business_name": name, "contact_name": "Pilot Test Owner",
        "email": email, "industry": industry, "consent": consent,
    }
    fields.update(other)
    return client.post("/signup", data=fields)


def _accept(client, fake, address, password="correct horse battery staple"):
    matches = [m for m in fake.messages if m[0] == address]
    assert len(matches) == 1
    body = matches[0][2]
    token = re.search(r"/accept-invite\?token=([A-Za-z0-9_-]+)", body)
    assert token, "Provider got no usable private invitation"
    path = "/accept-invite?token=" + token.group(1)
    assert client.get(path).status_code == 200
    result = client.post("/accept-invite", data={
        "token": token.group(1), "name": "Pilot Test Owner", "password": password,
    })
    assert result.status_code == 303
    return token.group(1)


def test_two_verified_purchasers_get_account_email_without_assessment(platform, mail_ready):
    alpha = client_for(platform)
    beta = client_for(platform)
    assert alpha.get("/signup").status_code == 200
    assert 'Email my account verification link' in alpha.get("/signup").text
    assert alpha.get("/buy").status_code == 200
    assert "https://buy.stripe.com/4gMaEQ5vs1Kk5lsdJu0Ny01" in alpha.get("/buy").text
    assert '/signup' in alpha.get("/").text

    email_a = "alpha-beta@example.test"
    email_b = "beta-beta@example.test"
    _verified_payment_claim(email_a)
    _verified_payment_claim(email_b)
    assert _signup(alpha, name="Alpha Beta Studio", email=email_a,
                   industry="construction").headers["location"] == "/signup/thanks"
    assert _signup(beta, name="Beta Cleaning Studio", email=email_b,
                   industry="cleaning").status_code == 303
    assert len(mail_ready.messages) == 2

    with session_scope() as db:
        messages = db.scalars(select(EmailMessage).where(EmailMessage.to_email.in_(
            [email_a, email_b]))).all()
        assert len(messages) == 2
        assert all(AUTH_LINK_PLACEHOLDER in m.body for m in messages)
        assert all(m.status == "sent" and m.provider == "resend" for m in messages)
        assert all(m.auth_token_id is not None for m in messages)
        ids = [m.org_id for m in messages]
        assert db.scalar(select(func.count()).select_from(ServiceOrder).where(
            ServiceOrder.org_id.in_(ids))) == 0
        assert ids[0] != ids[1]
        for org_id in ids:
            db.set_context(org_id, None)
            org = db.get(Organization, org_id)
            assert org and org.plan == "purchase_pending"
            assert org.settings["billing"]["paid"] is False
            db.set_context(None, None)

    # Neither signup has authenticated until the owner controls the email.
    assert alpha.get("/app").status_code == 303
    assert beta.get("/app/feedback").status_code == 303

    token_a = _accept(alpha, mail_ready, email_a)
    token_b = _accept(beta, mail_ready, email_b)
    assert alpha.get("/app").status_code == 200
    assert beta.get("/app").status_code == 200
    assert alpha.get("/app/feedback").status_code == 200
    assert beta.get("/app/feedback").status_code == 200
    assert alpha.get("/operator").status_code == 404
    assert "Alpha Beta Studio" in alpha.get("/app/settings").text
    assert "Beta Cleaning Studio" not in alpha.get("/app/settings").text
    assert "Beta Cleaning Studio" in beta.get("/app/settings").text
    assert "Alpha Beta Studio" not in beta.get("/app/settings").text

    # The authentication tokens cannot be replayed into another session.
    stranger = client_for(platform)
    assert stranger.get("/accept-invite?token=" + token_a).status_code == 410
    assert stranger.get("/accept-invite?token=" + token_b).status_code == 410


def test_beta_duplicate_owner_pending_suppressed_and_rate_limited(platform, mail_ready):
    visitor = client_for(platform)
    e = "repeat-beta@example.test"
    _verified_payment_claim(e)
    assert _signup(visitor, name="Once Only Studio", email=e).status_code == 303
    assert _signup(visitor, name="Duplicate Brand", email=e).status_code == 303
    assert _signup(visitor, name="Duplicate Brand", email=e).status_code == 303
    result = _signup(visitor, name="Duplicate Brand", email=e)
    assert result.status_code == 429
    assert len(mail_ready.messages) == 1
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(EmailMessage).where(
            EmailMessage.to_email == e)) == 1
        assert db.scalar(select(func.count()).select_from(AuthToken).where(
            AuthToken.email == e, AuthToken.purpose == "invite")) == 1


def test_beta_input_protection_and_provider_off(platform, mail_ready, monkeypatch):
    visitor = client_for(platform)
    bad = _signup(visitor, name="", email="invalid", consent="")
    assert bad.status_code == 422
    assert not mail_ready.messages

    trap = _signup(visitor, name="Trapped Studio", email="trap@example.test",
                   company_site="robot pasted this")
    assert trap.status_code == 303
    assert not mail_ready.messages

    def unavailable(settings=None):
        return None

    monkeypatch.setattr("app.routers.beta_signup.get_provider", unavailable)
    assert "temporarily unavailable" in visitor.get("/signup").text
    result = _signup(visitor, name="Provider Down", email="down@example.test")
    assert result.status_code == 503
    assert not mail_ready.messages



def test_anonymous_signup_without_verified_stripe_purchase_creates_no_account(
    platform, mail_ready,
):
    visitor = client_for(platform)
    response = _signup(
        visitor, name="Unpaid Business", email="unpaid@example.test")
    assert response.status_code == 422
    assert "Complete Business Workspace checkout first" in response.text
    assert not mail_ready.messages
    with session_scope() as db:
        assert not db.scalar(select(AuthToken).where(
            AuthToken.email == "unpaid@example.test"))


def test_pending_invitation_resends_after_cooldown_without_creating_new_tenant(
    platform, mail_ready,
):
    visitor = client_for(platform)
    email = "retry-beta@example.test"
    _verified_payment_claim(email)
    assert _signup(visitor, name="Original Brand", email=email).status_code == 303
    assert len(mail_ready.messages) == 1
    old_message = mail_ready.messages[0][2]
    old_token = re.search(r"/accept-invite\?token=([A-Za-z0-9_-]+)", old_message).group(1)
    with session_scope() as db:
        first = db.scalar(select(EmailMessage).where(
            EmailMessage.to_email == email, EmailMessage.kind == "invite"))
        original_org, token_id = first.org_id, first.auth_token_id
        first.created_at = now() - timedelta(minutes=4)
    assert _signup(visitor, name="Duplicate Brand", email=email).status_code == 303
    assert len(mail_ready.messages) == 2
    new_message = mail_ready.messages[-1][2]
    new_token = re.search(r"/accept-invite\?token=([A-Za-z0-9_-]+)", new_message).group(1)
    assert new_token != old_token
    assert visitor.get("/accept-invite?token="+old_token).status_code == 410
    assert visitor.get("/accept-invite?token="+new_token).status_code == 200
    with session_scope() as db:
        rows = db.scalars(select(EmailMessage).where(
            EmailMessage.to_email == email, EmailMessage.kind == "invite")).all()
        assert len(rows) == 2
        assert {row.org_id for row in rows} == {original_org}
        assert {row.auth_token_id for row in rows} == {token_id}
        assert db.scalar(select(func.count()).select_from(AuthToken).where(
            AuthToken.email == email, AuthToken.purpose == "invite")) == 1


def test_resend_under_three_minute_cooldown_does_not_rotate_a_live_link(
    platform, mail_ready,
):
    visitor = client_for(platform)
    email = "cooldown-beta@example.test"
    _verified_payment_claim(email)
    assert _signup(visitor, name="Cooldown Studio", email=email).status_code == 303
    assert _signup(visitor, name="Cooldown Studio", email=email).status_code == 303
    assert len(mail_ready.messages) == 1
