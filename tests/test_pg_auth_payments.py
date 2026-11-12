"""Authentication, sessions, payments, email and the job queue on real PostgreSQL.

No real payment or email provider is contacted. Stripe and Resend are exercised through
httpx mock transports and locally signed webhook payloads, and the tests assert that
nothing is ever recorded as paid or sent without provider evidence.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from .conftest import client_for, csrf_of, last_email_link, login, make_operator, onboard_business

PASSWORD = "correct horse battery staple"


# -------------------------------------------------------------------- sign-in/session

def test_login_errors_are_generic_and_rate_limited(platform):
    onboard_business(platform, business="Login Test Co", owner_email="owner@logintest.example")
    anon = client_for(platform)
    wrong = anon.post("/login", data={"email": "owner@logintest.example", "password": "not the password"})
    unknown = anon.post("/login", data={"email": "nobody@logintest.example", "password": "not the password"})
    assert wrong.status_code == unknown.status_code == 401
    marker = "don&#39;t match an account"
    assert marker in wrong.text and marker in unknown.text       # same answer: no account enumeration
    assert "mf_session" not in wrong.cookies
    statuses = [anon.post("/login", data={"email": "victim@logintest.example", "password": "guess-%d" % i}).status_code
                for i in range(10)]
    assert statuses[:8] == [401] * 8 and statuses[-1] == 429     # per-account throttle


def test_session_cookie_flags_logout_and_open_redirect(platform):
    onboard_business(platform, business="Cookie Test Co", owner_email="owner@cookietest.example")
    anon = client_for(platform)
    response = anon.post("/login", data={"email": "owner@cookietest.example", "password": PASSWORD,
                                         "next": "//evil.example/steal"})
    assert response.status_code == 303 and response.headers["location"] == "/app"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    for hostile in ("https://evil.example", "/\\evil.example", "//evil.example"):
        page = client_for(platform).get("/login", params={"next": hostile})
        assert 'name="next" value="/app"' in page.text
    token = anon.cookies.get("mf_session")
    assert anon.get("/app").status_code == 200
    assert anon.post("/logout", data={"csrf": csrf_of(anon)}).status_code == 303
    # The old token is dead on the server, not just removed from the browser.
    replay = client_for(platform)
    replay.cookies.set("mf_session", token)
    assert replay.get("/app").status_code == 303
    # Garbage and oversized tokens are simply anonymous.
    for junk in ("x", "A" * 5000, "../../etc/passwd"):
        probe = client_for(platform)
        probe.cookies.set("mf_session", junk)
        assert probe.get("/app").status_code == 303


def test_tokens_are_stored_hashed(platform):
    from app.db import session_scope
    from app.models import AuthToken, Session
    onboard_business(platform, business="Hash Test Co", owner_email="owner@hashtest.example")
    owner = login(platform, "owner@hashtest.example", PASSWORD)
    raw = owner.cookies.get("mf_session")
    with session_scope() as db:
        hashes = set(db.scalars(select(Session.token_hash))) | set(db.scalars(select(AuthToken.token_hash)))
        assert raw not in hashes and all(len(h) == 64 for h in hashes)
        from app.models import User
        stored = db.scalar(select(User.password_hash).where(User.email == "owner@hashtest.example"))
        assert stored.startswith("$argon2id$") and PASSWORD not in stored


def test_password_reset_flow(platform):
    from app.db import session_scope
    from app.models import EmailMessage
    onboard_business(platform, business="Reset Test Co", owner_email="owner@resettest.example")
    old_session = login(platform, "owner@resettest.example", PASSWORD)
    anon = client_for(platform)
    with session_scope() as db:
        before = db.scalar(select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "password_reset"))
    known = anon.post("/forgot", data={"email": "owner@resettest.example"})
    unknown = anon.post("/forgot", data={"email": "ghost@resettest.example"})
    assert known.status_code == unknown.status_code == 200
    assert "If an account exists" in known.text and "If an account exists" in unknown.text
    assert "No message was sent".lower() in known.text.lower()   # honest: no provider configured
    with session_scope() as db:
        after = db.scalar(select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "password_reset"))
        assert after == before + 1                                # nothing created for the unknown address
    link = last_email_link("owner@resettest.example", "/reset")
    token = link.split("token=")[1]
    assert anon.get(link).status_code == 200
    assert anon.post("/reset", data={"token": token, "password": "short"}).status_code == 422
    new_password = "a brand new long passphrase"
    assert anon.post("/reset", data={"token": token, "password": new_password}).status_code == 303
    assert anon.post("/reset", data={"token": token, "password": "another long passphrase 2"}).status_code == 410  # single use
    assert old_session.get("/app").status_code == 303            # every existing session was revoked
    assert client_for(platform).post("/login", data={"email": "owner@resettest.example", "password": PASSWORD}).status_code == 401
    login(platform, "owner@resettest.example", new_password)
    assert anon.get("/reset?token=not-a-real-token-000000000000").status_code == 410


def test_invites_are_single_use_and_bound_to_their_email(platform):
    owner, org_id, _ = onboard_business(platform, business="Invite Test Co", owner_email="owner@invitetest.example")
    owner.post("/app/team/invite", data={"csrf": csrf_of(owner), "email": "new@invitetest.example", "role": "staff"})
    link = last_email_link("new@invitetest.example", "/accept-invite")
    token = link.split("token=")[1]
    joiner = client_for(platform)
    assert joiner.post("/accept-invite", data={"token": token, "name": "New Person", "password": "tiny"}).status_code == 422
    assert joiner.post("/accept-invite", data={"token": token, "name": "New Person", "password": PASSWORD}).status_code == 303
    me = joiner.get("/api/v1/me").json()
    assert me["user"]["email"] == "new@invitetest.example" and me["role"] == "staff"   # email comes from the token
    assert client_for(platform).post("/accept-invite", data={"token": token, "name": "Thief", "password": PASSWORD}).status_code == 410
    assert client_for(platform).get("/accept-invite?token=" + "z" * 43).status_code == 410


def test_security_headers_and_host_routing(platform):
    page = client_for(platform).get("/")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "object-src 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in page.headers
    owner, _, _ = onboard_business(platform, business="Header Test Co", owner_email="owner@headertest.example")
    assert owner.get("/app").headers["cache-control"] == "no-store"
    # The same session cookie presented on a customer-site hostname unlocks nothing.
    other = client_for(platform, "anything.sites.localhost")
    other.cookies.set("mf_session", owner.cookies.get("mf_session"))
    assert other.get("/app").status_code == 404 and other.get("/api/v1/leads").status_code == 404
    assert client_for(platform, "anything.sites.localhost").get("/healthz").json() == {"status": "ok"}
    # Errors never include stack traces or SQL.
    missing = owner.get("/app/inquiries/not-a-uuid")
    assert missing.status_code == 404 and "Traceback" not in missing.text and "SELECT" not in missing.text


# --------------------------------------------------------------------------- payments

WEBHOOK_SECRET = "whsec_test_fictional"


@pytest.fixture()
def stripe(monkeypatch):
    """Stripe adapter wired to a mock transport. No network, no real keys."""
    from app.services import payments
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "cs_test_" + uuid.uuid4().hex, "url": "https://checkout.stripe.com/c/pay/test"})

    provider = payments.StripeProvider("sk_test_fictional", WEBHOOK_SECRET,
                                       client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(payments, "get_provider", lambda settings=None: provider)
    return calls


def _start_paid_order(platform, email: str, business: str):
    from app.db import session_scope
    from app.models import ServiceOrder
    visitor = client_for(platform)
    response = visitor.post("/assessment", data={
        "excited": "We are growing our residential cleaning business quickly.",
        "blocker": "Our website is outdated and I miss leads because I forget to follow up.",
        "business_name": business, "contact_name": "Paying Person", "email": email, "industry": "cleaning", "consent": "yes"})
    token = response.headers["location"].rsplit("/", 1)[1]
    ordered = visitor.post(f"/assessment/r/{token}/order",
                           data={"offer_code": "launch", "approved_name": "Paying Person", "agree": "yes"})
    with session_scope() as db:
        order = db.scalar(select(ServiceOrder).order_by(ServiceOrder.created_at.desc()))
        return visitor, token, ordered, order.id, order.provider_ref


def _event(session_id: str, *, amount: int = 19900, status: str = "paid", event_id: str | None = None) -> bytes:
    return json.dumps({"id": event_id or "evt_" + uuid.uuid4().hex, "type": "checkout.session.completed",
                       "data": {"object": {"id": session_id, "payment_status": status, "amount_total": amount}}}).encode()


def _post_webhook(platform, payload: bytes, signature: str):
    return client_for(platform).post("/webhooks/stripe", content=payload,
                                     headers={"Stripe-Signature": signature, "Content-Type": "application/json"})


def test_stripe_checkout_and_webhook_lifecycle(platform, stripe):
    from app.db import session_scope
    from app.models import EmailMessage, Organization, ServiceOrder
    from app.services.payments import sign_for_test
    visitor, token, ordered, order_id, session_id = _start_paid_order(platform, "payer@paytest.example", "Pay Test Cleaning")
    assert ordered.status_code == 303 and ordered.headers["location"].startswith("https://checkout.stripe.com/")
    sent = stripe[0]
    assert sent.headers["idempotency-key"] == f"mf-order-{order_id}"
    assert b"unit_amount%5D=19900" in sent.content and b"sk_test" not in sent.content

    def status():
        with session_scope() as db:
            order = db.get(ServiceOrder, order_id)
            return order.status, order.paid_at, order.org_id

    # Returning from checkout proves nothing: the order stays unpaid until a signed webhook.
    assert "Waiting for payment confirmation" in visitor.get(f"/order/{order_id}?t={token}").text
    assert status()[0] == "payment_pending"

    now = int(time.time())
    good = _event(session_id)
    for label, payload, signature in (
        ("unsigned", good, ""),
        ("wrong secret", good, sign_for_test("whsec_attacker", good, now)),
        ("stale timestamp", good, sign_for_test(WEBHOOK_SECRET, good, now - 3600)),
        ("tampered body", good.replace(b"19900", b"19901"), sign_for_test(WEBHOOK_SECRET, good, now)),
    ):
        assert _post_webhook(platform, payload, signature).status_code == 400, label
        assert status()[0] == "payment_pending", label
    # Correctly signed but not actually paid, or for the wrong amount: still unpaid.
    for payload in (_event(session_id, status="unpaid"), _event(session_id, amount=100)):
        response = _post_webhook(platform, payload, sign_for_test(WEBHOOK_SECRET, payload, now))
        assert response.status_code == 200 and response.json()["outcome"] in ("unpaid", "amount_mismatch")
        assert status()[0] == "payment_pending"

    event_id = "evt_" + uuid.uuid4().hex
    paid = _event(session_id, event_id=event_id)
    first = _post_webhook(platform, paid, sign_for_test(WEBHOOK_SECRET, paid, now))
    assert first.json()["outcome"] == "paid"
    state, paid_at, org_id = status()
    assert state == "provisioned" and paid_at is not None and org_id is not None   # job ran: workspace exists
    # Stripe retries deliveries: a replay changes nothing and creates no second workspace.
    again = _post_webhook(platform, paid, sign_for_test(WEBHOOK_SECRET, paid, now))
    assert again.json()["outcome"] == "duplicate"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(EmailMessage).where(
            EmailMessage.to_email == "payer@paytest.example", EmailMessage.kind == "invite")) == 1
        assert db.scalar(select(ServiceOrder.payment_evidence).where(ServiceOrder.id == order_id)) == "stripe:" + event_id
    with session_scope(org_id=org_id) as db:
        org = db.scalar(select(Organization))
        assert org.settings["billing"] == {"order_id": str(order_id), "paid": True, "amount_cents": 19900,
                                           "offer": "Website and Inquiry Desk setup"}
    assert "Your workspace is ready" in visitor.get(f"/order/{order_id}?t={token}").text


def test_without_a_provider_nothing_is_ever_marked_paid_automatically(platform):
    from app.db import session_scope
    from app.models import ServiceOrder
    visitor, token, ordered, order_id, provider_ref = _start_paid_order(platform, "manual@paytest.example", "Manual Pay Co")
    assert provider_ref is None and f"/order/{order_id}" in ordered.headers["location"]
    page = visitor.get(ordered.headers["location"]).text
    assert "No payment has been taken" in page and "Not yet received" in page
    assert _post_webhook(platform, b"{}", "t=1,v1=00").status_code == 404   # webhook endpoint is off

    email, password = make_operator()
    op = login(platform, email, password)
    csrf = csrf_of(op)
    # Provisioning an unpaid order needs an explicit reason; a payment needs a reference.
    op.post(f"/operator/orders/{order_id}/provision", data={"csrf": csrf, "reason": ""})
    op.post(f"/operator/orders/{order_id}/mark-paid", data={"csrf": csrf, "reference": " "})
    with session_scope() as db:
        order = db.get(ServiceOrder, order_id)
        assert order.status == "approved_unpaid" and order.org_id is None and order.paid_at is None
    assert op.post(f"/operator/orders/{order_id}/mark-paid", data={"csrf": csrf, "reference": "Check 1042"}).status_code == 303
    with session_scope() as db:
        order = db.get(ServiceOrder, order_id)
        assert order.status == "provisioned" and order.paid_at is not None
        assert "Check 1042" in order.payment_evidence and email in order.payment_evidence
    # Paying twice is refused rather than double-counted.
    op.post(f"/operator/orders/{order_id}/mark-paid", data={"csrf": csrf, "reference": "Check 1043"})
    with session_scope() as db:
        assert "Check 1042" in db.get(ServiceOrder, order_id).payment_evidence


def test_one_open_order_per_assessment(platform):
    from app.db import session_scope
    from app.models import ServiceOrder
    visitor, token, _, order_id, _ = _start_paid_order(platform, "dupe@paytest.example", "Dupe Order Co")
    again = visitor.post(f"/assessment/r/{token}/order", data={"offer_code": "operate", "approved_name": "X Y", "agree": "yes"})
    assert again.status_code == 303 and str(order_id) in again.headers["location"]
    bad = client_for(platform).post(f"/assessment/r/{token}/order", data={"offer_code": "launch", "approved_name": "X Y"})
    assert bad.status_code == 303                                  # existing order wins before validation
    with session_scope() as db:
        from app.models import Assessment
        from app.security import digest
        assessment_id = db.scalar(select(Assessment.id).where(Assessment.token_hash == digest(token)))
        assert db.scalar(select(func.count()).select_from(ServiceOrder).where(ServiceOrder.assessment_id == assessment_id)) == 1


# ------------------------------------------------------------------- email and jobs

def test_email_is_only_marked_sent_with_provider_evidence(pg_database):
    from app.db import session_scope
    from app.services.email import ResendProvider, deliver, queue_email

    def provider(handler):
        return ResendProvider("re_test_fictional", "MeasuredField <hello@measuredfield.test>",
                              client=httpx.Client(transport=httpx.MockTransport(handler)))

    seen = []

    def accept(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "msg_123"})

    with session_scope() as db:
        none = deliver(db, queue_email(db, kind="test", to="a@mail.example", subject="s", body="b"), provider=None)
        assert none.status == "not_sent_no_provider" and none.sent_at is None
        ok = deliver(db, queue_email(db, kind="test", to="b@mail.example", subject="s", body="b"), provider(accept))
        assert (ok.status, ok.provider_message_id) == ("sent", "msg_123") and ok.sent_at is not None
        assert seen[0]["to"] == ["b@mail.example"]
        deliver(db, ok, provider(accept))
        assert len(seen) == 1                                       # already sent: not sent twice
        failed = deliver(db, queue_email(db, kind="test", to="c@mail.example", subject="s", body="b"),
                         provider(lambda r: httpx.Response(500, json={})))
        assert failed.status == "failed" and failed.sent_at is None
        no_id = deliver(db, queue_email(db, kind="test", to="d@mail.example", subject="s", body="b"),
                        provider(lambda r: httpx.Response(200, json={})))
        assert no_id.status == "failed"

        def boom(request):
            raise httpx.ConnectError("no network in tests")
        down = deliver(db, queue_email(db, kind="test", to="e@mail.example", subject="s", body="b"), provider(boom))
        assert down.status == "failed" and "ConnectError" in down.error


def test_job_queue_is_idempotent_and_retries_with_backoff(pg_database, monkeypatch):
    from app.db import session_scope
    from app.models import Job
    from app.services import jobs
    key = "test-job:" + uuid.uuid4().hex
    attempts = []

    def flaky(job):
        attempts.append(job.attempts)
        if len(attempts) < 2:
            raise RuntimeError("transient failure with customer@secret.example inside")

    monkeypatch.setitem(jobs.HANDLERS, "flaky_test", flaky)
    with session_scope() as db:
        for _ in range(3):
            jobs.enqueue(db, "flaky_test", {"n": 1}, key=key)
        jobs.enqueue(db, "no_such_kind", {}, key=key + ":unknown")
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.idempotency_key == key)) == 1
    jobs.run_due(10)
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.idempotency_key == key))
        assert job.status == "queued" and job.attempts == 1 and job.run_after > job.created_at   # backed off
        job.run_after = job.created_at                                                           # fast-forward
        unknown = db.scalar(select(Job).where(Job.idempotency_key == key + ":unknown"))
        assert unknown.status == "queued" and "unknown job kind" in unknown.last_error
    jobs.run_due(10)
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.idempotency_key == key))
        assert job.status == "done" and attempts == [1, 2]
        # Exhausted retries end in 'failed', never silently dropped.
        unknown = db.scalar(select(Job).where(Job.idempotency_key == key + ":unknown"))
        unknown.attempts, unknown.run_after = jobs.MAX_ATTEMPTS - 1, unknown.created_at
    jobs.run_due(10)
    with session_scope() as db:
        assert db.scalar(select(Job.status).where(Job.idempotency_key == key + ":unknown")) == "failed"


def test_provisioning_is_idempotent(platform):
    from app.db import session_scope
    from app.models import AssessmentReport, Organization, ServiceOrder
    from app.services import orders
    _, org_id, _ = onboard_business(platform, business="Idempotent Test Co", owner_email="owner@idem.example")
    with session_scope() as db:
        order = db.scalar(select(ServiceOrder).where(ServiceOrder.org_id == org_id))
        again = orders.provision(db, order)
        assert again.id == org_id
    with session_scope(org_id=org_id) as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 1
        assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 1
