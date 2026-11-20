"""MF-087: signed direct Stripe links -> pending proof -> verified owner claim.

Uses disposable real PostgreSQL and local synthetic Stripe HTTP, not live money.
"""
import json
import time
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import (AuthToken, BillingSubscription, Organization,
                        PaymentEvent, PendingPaymentLinkClaim, ServiceOrder, now)
from app.services.orders import active_subscription_for
from app.services.payments import StripeProvider, sign_for_test

from .conftest import csrf_of, onboard_business

SECRET = "whsec_fictional_direct123"
LINK = "plink_1UcxSA2LWll6Mkhu7MR1P3dr"
PRICE = "price_1Ub6rZ2LWll6MkhusWBI1ebk"
SUB = "sub_syntheticlink_abc12345"
CUS = "cus_syntheticlink_abc12345"
SESSION = "cs_test_syntheticlink_abc12345"


@pytest.fixture
def stripe_settings(monkeypatch):
    monkeypatch.setenv("MF_PAYMENT_PROVIDER", "stripe")
    monkeypatch.setenv("MF_STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("MF_STRIPE_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _member(platform, label, *, proof=True):
    email = label + "-" + uuid.uuid4().hex[:8] + "@fictional.test"
    owner, org_id, _ = onboard_business(
        platform, business="Synthetic " + label + " " + uuid.uuid4().hex[:7],
        owner_email=email)
    with session_scope(org_id=org_id) as db:
        org = db.get(Organization, org_id)
        org.plan = "founding_beta"
        org.settings = {"billing": {"paid": False, "offer": "Founding beta",
                                   "amount_cents": 0}}
    if proof:
        with session_scope() as db:
            db.add(AuthToken(
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                purpose="invite", email=email, org_id=org_id, role="owner",
                expires_at=now() + timedelta(hours=1), used_at=now(),
                issued_at=now(), issued_via="email",
            ))
    return owner, org_id, email


def _event(email, *, payment_status="no_payment_required", amount_total=0,
           discount=9900, event_id=None, session=SESSION, link=LINK,
           subscription=SUB, customer=CUS):
    return {"id": event_id or "evt_" + uuid.uuid4().hex,
            "type": "checkout.session.completed", "created": int(time.time()),
            "data": {"object": {
                "id": session, "payment_link": link, "mode": "subscription",
                "currency": "usd", "customer": customer, "subscription": subscription,
                "payment_status": payment_status, "amount_subtotal": 9900,
                "amount_total": amount_total,
                "total_details": {"amount_discount": discount, "amount_tax": 0},
                "customer_details": {"email": email},
            }}}


def _send(client, event):
    body = json.dumps(event, separators=(",", ":")).encode()
    sig = sign_for_test(SECRET, body, int(time.time()))
    return client.post("/webhooks/stripe", content=body,
                       headers={"stripe-signature": sig,
                                "content-type": "application/json"})


@pytest.fixture
def fake_stripe(stripe_settings, monkeypatch):
    state = {"line_price": PRICE, "status": "active",
             "session": "cs_test_" + uuid.uuid4().hex,
             "sub": "sub_" + uuid.uuid4().hex,
             "customer": "cus_" + uuid.uuid4().hex,
             "period_end": int((now() + timedelta(days=30)).timestamp()),
             "fail": False}
    calls = []
    def handler(request: httpx.Request):
        calls.append(request.url.path)
        if state["fail"]:
            return httpx.Response(503, json={"error":"retry"})
        if request.url.path.endswith("/line_items"):
            return httpx.Response(200, json={
                "has_more": False,
                "data": [{"quantity": 1, "price": {
                    "id": state["line_price"], "currency": "usd",
                    "unit_amount": 9900, "recurring": {"interval":"month"},
                }}]})
        if request.url.path.endswith("/" + state["sub"]):
            return httpx.Response(200, json={
                "id": state["sub"], "customer": state["customer"], "status": state["status"],
                "current_period_end": state["period_end"],
            })
        return httpx.Response(404, json={"error":"unknown"})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = StripeProvider("sk_test_synthetic", SECRET, client=http)
    monkeypatch.setattr("app.services.payments.get_provider", lambda settings=None: provider)
    yield state, calls
    http.close()


def test_signed_purchase_claims_existing_verified_owner_no_new_business(
        platform, fake_stripe):
    owner, org_id, email = _member(platform, "buyer")
    stranger, other_org, other_email = _member(platform, "stranger")
    state, calls = fake_stripe
    event = _event(email, session=state["session"], subscription=state["sub"],
                   customer=state["customer"])
    receipt = _send(owner, event)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["outcome"] == "direct_checkout_pending_claim"
    assert _send(owner, event).json()["outcome"] == "duplicate"

    with session_scope(org_id=org_id) as db:
        claims = db.scalars(select(PendingPaymentLinkClaim)).all()
        assert len(claims) == 1
        claim = claims[0]
        claim_id = claim.id
        assert claim.status == "pending_claim" and claim.org_id is None
        assert claim.billing_email == email and claim.amount_total_cents == 0
        assert db.scalar(select(func.count()).select_from(Organization)) == 1  # RLS tenant view
        assert not active_subscription_for(db, org_id, "operate")
        assert claim.order_id is None
    assert "Connect this purchase" in owner.get("/app/billing/claim").text
    assert "Connect this purchase" not in stranger.get("/app/billing/claim").text
    assert owner.post("/app/billing/claim/" + str(claim_id),
                      data={"csrf":"invalid","confirm":"yes"}).status_code == 403
    denied = stranger.post("/app/billing/claim/" + str(claim_id),
                           data={"csrf":csrf_of(stranger),"confirm":"yes"})
    assert denied.status_code == 303
    with session_scope() as db:
        assert db.get(PendingPaymentLinkClaim, claim_id).status == "pending_claim"

    purchased = owner.post("/app/billing/claim/" + str(claim_id),
                           data={"csrf":csrf_of(owner),"confirm":"yes"})
    assert purchased.status_code == 303
    assert "/app/settings" in purchased.headers["location"]
    with session_scope(org_id=org_id) as db:
        claim = db.get(PendingPaymentLinkClaim, claim_id)
        assert claim.org_id == org_id and claim.status == "claimed"
        order = db.get(ServiceOrder, claim.order_id)
        assert order.org_id == org_id and order.assessment_id is None
        assert order.provider_ref == state["session"] and order.settled_amount_cents == 0
        assert order.offer_code == "operate" and order.payment_provider == "stripe"
        subscription = db.scalar(select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == state["sub"]))
        assert subscription.order_id == order.id and subscription.org_id == org_id
        assert active_subscription_for(db, org_id, "operate")
        org = db.get(Organization, org_id)
        assert org.plan == "operate"
        assert org.settings["billing"]["paid"] is True
        assert org.settings["billing"]["amount_cents"] == 0
        assert org.settings["owner_checkout_previous_plan"] == "founding_beta"
    assert "Manage billing in Stripe" in owner.get("/app/settings").text
    again = owner.post("/app/billing/claim/" + str(claim_id),
                       data={"csrf":csrf_of(owner),"confirm":"yes"})
    assert again.status_code == 303
    with session_scope(org_id=org_id) as db:
        assert db.scalar(select(func.count()).select_from(ServiceOrder).where(
            ServiceOrder.provider_ref == state["session"])) == 1

    cancel = {"id": "evt_synthetic_cancel_" + uuid.uuid4().hex,
              "type": "customer.subscription.deleted", "created": int(time.time()) + 5,
              "data": {"object": {"id": state["sub"], "customer": state["customer"],
                                  "status":"canceled"}}}
    result = _send(owner, cancel)
    assert result.status_code == 200
    with session_scope(org_id=org_id) as db:
        assert not active_subscription_for(db, org_id, "operate")
        org = db.get(Organization, org_id)
        assert org.plan == "founding_beta"
        assert org.settings["billing"]["paid"] is False


def test_transient_stripe_verification_rolls_back_event_before_retry(
        platform, fake_stripe):
    owner, org_id, email = _member(platform, "transient")
    state, calls = fake_stripe
    event = _event(email, session=state["session"], subscription=state["sub"],
                   customer=state["customer"])
    state["fail"] = True
    result = _send(owner, event)
    assert result.status_code == 503
    with session_scope() as db:
        assert db.scalar(select(PaymentEvent.id).where(
            PaymentEvent.event_id == event["id"])) is None
        assert db.scalar(select(PendingPaymentLinkClaim.id).where(
            PendingPaymentLinkClaim.checkout_session_id == state["session"])) is None
    state["fail"] = False
    retry = _send(owner, event)
    assert retry.status_code == 200
    assert retry.json()["outcome"] == "direct_checkout_pending_claim"


def test_wrong_price_and_unverified_buyer_cannot_claim(platform, fake_stripe):
    owner, org_id, email = _member(platform, "wrongprice", proof=False)
    state, calls = fake_stripe
    state["line_price"] = "price_wrong_synthetic"
    invalid = _send(owner, _event(email, session=state["session"],
                                        subscription=state["sub"], customer=state["customer"]))
    assert invalid.status_code == 200
    assert invalid.json()["outcome"] == "direct_checkout_price_mismatch"
    with session_scope() as db:
        assert db.scalar(select(PendingPaymentLinkClaim.id).where(
            PendingPaymentLinkClaim.checkout_session_id == state["session"])) is None
    state["line_price"] = PRICE
    valid = _send(owner, _event(email, event_id="evt_" + uuid.uuid4().hex,
                                      session=state["session"],
                                      subscription=state["sub"], customer=state["customer"]))
    assert valid.json()["outcome"] == "direct_checkout_pending_claim"
    page = owner.get("/app/billing/claim")
    assert page.status_code == 200
    assert "does not yet have a recorded email-delivered verification" in page.text
    with session_scope() as db:
        claim = db.scalar(select(PendingPaymentLinkClaim).where(
            PendingPaymentLinkClaim.checkout_session_id == state["session"]))
    attempt = owner.post("/app/billing/claim/" + str(claim.id),
                         data={"csrf":csrf_of(owner),"confirm":"yes"})
    assert attempt.status_code == 303
    with session_scope() as db:
        claim = db.get(PendingPaymentLinkClaim, claim.id)
        assert claim.status == "pending_claim" and claim.order_id is None


def test_growth_partner_requires_active_operate_and_correct_price(platform, fake_stripe):
    owner, org_id, email = _member(platform, "growthaddon")
    state, calls = fake_stripe
    state["line_price"] = "price_1Ub6rh2LWll6MkhuYiaLXu2m"
    incoming = _event(email, link="plink_1UcxUD2LWll6MkhuecqI4wT9",
                      session=state["session"], subscription=state["sub"],
                      customer=state["customer"], amount_total=9900,
                      discount=0, payment_status="paid")
    created = _send(owner, incoming)
    assert created.status_code == 200
    assert created.json()["outcome"] == "direct_checkout_pending_claim"
    with session_scope() as db:
        pending = db.scalar(select(PendingPaymentLinkClaim).where(
            PendingPaymentLinkClaim.checkout_session_id == state["session"]))
        assert pending.offer_code == "growth_partner"
        claim_id = pending.id
    refused = owner.post("/app/billing/claim/" + str(claim_id),
                         data={"csrf": csrf_of(owner), "confirm": "yes"})
    assert refused.status_code == 303
    with session_scope() as db:
        pending = db.get(PendingPaymentLinkClaim, claim_id)
        assert pending.status == "pending_claim" and pending.order_id is None
    with session_scope(org_id=org_id) as db:
        assert not active_subscription_for(db, org_id, "operate")
        assert db.get(Organization, org_id).plan == "founding_beta"


def test_terminal_cancellation_before_claim_never_grants_access(platform, fake_stripe):
    owner, org_id, email = _member(platform, "precancel")
    state, calls = fake_stripe
    paid = _send(owner, _event(email, session=state["session"],
                                subscription=state["sub"], customer=state["customer"]))
    assert paid.json()["outcome"] == "direct_checkout_pending_claim"
    with session_scope() as db:
        pending = db.scalar(select(PendingPaymentLinkClaim).where(
            PendingPaymentLinkClaim.checkout_session_id == state["session"]))
        claim_id = pending.id
    canceled = {"id": "evt_" + uuid.uuid4().hex,
                "type": "customer.subscription.deleted", "created": int(time.time()) + 5,
                "data": {"object": {"id": state["sub"], "customer": state["customer"],
                                    "status": "canceled"}}}
    assert _send(owner, canceled).json()["outcome"] == "subscription_updated"
    # Even if the provider adapter is stale and says active, the signed
    # terminal cancellation already persisted in BillingSubscription.
    attempt = owner.post("/app/billing/claim/" + str(claim_id),
                         data={"csrf": csrf_of(owner), "confirm": "yes"})
    assert attempt.status_code == 303
    with session_scope(org_id=org_id) as db:
        row = db.get(PendingPaymentLinkClaim, claim_id)
        assert row.order_id is None and row.org_id is None
        assert not active_subscription_for(db, org_id, "operate")
        assert db.get(Organization, org_id).plan == "founding_beta"


def test_invalid_payment_totals_and_unknown_link_never_create_claim(platform, fake_stripe):
    owner, org_id, email = _member(platform, "invalidlink")
    state, calls = fake_stripe
    session = state["session"]
    events = [
        _event(email, session=session, customer=state["customer"],
               subscription=state["sub"], discount=9800),
        _event(email, session=session, customer=state["customer"],
               subscription=state["sub"], link="plink_unknown_synthetic"),
        _event(email, session=session, customer=state["customer"],
               subscription=state["sub"], payment_status="unpaid"),
    ]
    expected = ["direct_checkout_invalid_amount",
                "not_an_allowlisted_payment_link",
                "unpaid"]
    for event, outcome in zip(events, expected):
        assert _send(owner, event).json()["outcome"] == outcome
    with session_scope() as db:
        assert db.scalar(select(PendingPaymentLinkClaim.id).where(
            PendingPaymentLinkClaim.checkout_session_id == session)) is None


def test_operator_review_reopens_only_after_fresh_provider_check(
        platform, fake_stripe):
    from .conftest import login, make_operator
    from app.models import PaymentLinkClaimReview
    owner, org_id, email = _member(platform, "supportrecheck")
    state, calls = fake_stripe
    event = _event(email, session=state["session"], subscription=state["sub"],
                   customer=state["customer"])
    assert _send(owner, event).json()["outcome"] == "direct_checkout_pending_claim"
    with session_scope() as db:
        claim = db.scalar(select(PendingPaymentLinkClaim).where(
            PendingPaymentLinkClaim.checkout_session_id == state["session"]))
        claim_id = claim.id
    state["status"] = "canceled"
    assert owner.post("/app/billing/claim/" + str(claim_id),
                      data={"csrf": csrf_of(owner), "confirm": "yes"}).status_code == 303
    with session_scope() as db:
        assert db.get(PendingPaymentLinkClaim, claim_id).status == "expired"
    # Ordinary owners cannot use the operator review path.
    assert owner.post("/operator/payment-link-claims/" + str(claim_id) + "/recheck",
                      data={"csrf": csrf_of(owner),
                            "reason": "I claim this purchase anyway"}).status_code == 404
    op_email, op_password = make_operator()
    operator = login(platform, op_email, op_password)
    review_page = operator.get("/operator/payment-link-claims")
    assert review_page.status_code == 200
    assert state["session"][-8:] in review_page.text
    state["status"] = "active"
    approved = operator.post("/operator/payment-link-claims/" + str(claim_id) + "/recheck",
                             data={"csrf": csrf_of(operator),
                                   "reason": "Stripe verified reinstated subscriber after customer support"})
    assert approved.status_code == 303
    with session_scope() as db:
        reopened = db.get(PendingPaymentLinkClaim, claim_id)
        assert reopened.status == "pending_claim"
        assert reopened.order_id is None and reopened.org_id is None
        trail = db.scalar(select(PaymentLinkClaimReview).where(
            PaymentLinkClaimReview.claim_id == claim_id))
        assert trail.outcome == "reopened" and trail.action == "recheck"
    assert owner.post("/app/billing/claim/" + str(claim_id),
                      data={"csrf": csrf_of(owner), "confirm": "yes"}).status_code == 303
    with session_scope(org_id=org_id) as db:
        assert active_subscription_for(db, org_id, "operate")


def test_incomplete_expired_signed_history_blocks_reclaim_even_if_provider_active(
        platform, fake_stripe):
    owner, org_id, email = _member(platform, "incompleteexpired")
    state, calls = fake_stripe
    first = _event(email, session=state["session"],
                   subscription=state["sub"], customer=state["customer"])
    assert _send(owner, first).json()["outcome"] == "direct_checkout_pending_claim"
    # A signed terminal event can arrive before the customer chooses a tenant.
    signed_terminal = {
        "id": "evt_" + uuid.uuid4().hex,
        "type": "customer.subscription.updated",
        "created": int(time.time()) + 5,
        "data": {"object": {"id": state["sub"], "customer": state["customer"],
                            "status": "incomplete_expired"}},
    }
    assert _send(owner, signed_terminal).json()["outcome"] == "subscription_updated"
    with session_scope() as db:
        claim = db.scalar(select(PendingPaymentLinkClaim).where(
            PendingPaymentLinkClaim.checkout_session_id == state["session"]))
        claim_id = claim.id
    attempt = owner.post("/app/billing/claim/" + str(claim_id),
                         data={"csrf": csrf_of(owner), "confirm": "yes"})
    assert attempt.status_code == 303
    with session_scope(org_id=org_id) as db:
        claim = db.get(PendingPaymentLinkClaim, claim_id)
        assert claim.order_id is None and claim.org_id is None
        assert not active_subscription_for(db, org_id, "operate")


def test_billing_claim_notice_contains_no_auth_token(platform, fake_stripe, monkeypatch):
    from app.models import EmailMessage
    owner, org_id, email = _member(platform, "notice")
    state, calls = fake_stripe
    # This changes only synthetic provider configuration, never sends email.
    settings = get_settings()
    monkeypatch.setattr(settings, "email_provider", "resend")
    event = _event(email, session=state["session"], subscription=state["sub"],
                   customer=state["customer"])
    result = _send(owner, event)
    assert result.status_code == 200
    assert result.json()["outcome"] == "direct_checkout_pending_claim"
    with session_scope() as db:
        msg = db.scalar(select(EmailMessage).where(
            EmailMessage.kind == "billing_claim", EmailMessage.to_email == email))
        assert msg is not None
        assert msg.status in ("queued", "not_sent_no_provider")  # no live email delivery
        assert "/app/billing/claim" in msg.body
        assert "token=" not in msg.body
        assert msg.auth_token_id is None
