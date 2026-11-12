"""MF-001: invitation and password-reset links are never stored in a usable form.

Real PostgreSQL, fictional accounts, mock email transports only. The tests prove that:
* nothing in the outbox (or anywhere an operator, owner or backup can read) redeems,
* a link exists only from provider send time, works exactly once and expires,
* a failed send or a retry never leaves an older link alive,
* the manual recovery procedure is restricted, short-lived and recorded,
* tokens are bound to their purpose, email address and organization.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from .conftest import client_for, csrf_of, last_email_link, login, make_operator, onboard_business

PASSWORD = "correct horse battery staple"
TOKEN_IN_TEXT = re.compile(r"token=([A-Za-z0-9_\-]{16,})")


def _resend(handler):
    from app.services.email import ResendProvider
    return ResendProvider("re_test_fictional", "MeasuredField <hello@measuredfield.test>",
                          client=httpx.Client(transport=httpx.MockTransport(handler)))


def _capture(sent: list, status: int = 200, payload: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(status, json=payload if payload is not None else {"id": "msg_" + uuid.uuid4().hex})
    return handler


def _message_for(db, to: str, kind: str):
    from app.models import EmailMessage
    return db.scalars(select(EmailMessage).where(EmailMessage.to_email == to, EmailMessage.kind == kind)
                      .order_by(EmailMessage.created_at.desc())).first()


def _request_reset(platform, email: str):
    assert client_for(platform).post("/forgot", data={"email": email}).status_code == 200


def _every_stored_string(db) -> str:
    """Everything an operator, a database reader or a backup could see about sign-in links."""
    rows = db.execute(text("SELECT coalesce(body,'') || ' ' || coalesce(subject,'') || ' ' || coalesce(error,'') FROM email_outbox")).all()
    rows += db.execute(text("SELECT payload::text || ' ' || coalesce(last_error,'') FROM jobs")).all()
    return "\n".join(r[0] for r in rows)


def test_outbox_never_holds_a_usable_link(platform):
    from app.db import session_scope
    from app.models import AuthToken
    from app.services.email import AUTH_LINK_PLACEHOLDER
    owner, org_id, _ = onboard_business(platform, business="Token Safe Co", owner_email="owner@tokensafe.example")
    owner.post("/app/team/invite", data={"csrf": csrf_of(owner), "email": "staff@tokensafe.example", "role": "staff"})
    _request_reset(platform, "owner@tokensafe.example")
    with session_scope() as db:
        stored = _every_stored_string(db)
        assert not TOKEN_IN_TEXT.search(stored), "a token value is stored in the outbox or job queue"
        for to, kind in (("staff@tokensafe.example", "invite"), ("owner@tokensafe.example", "password_reset")):
            message = _message_for(db, to, kind)
            assert AUTH_LINK_PLACEHOLDER in message.body and message.status == "not_sent_no_provider"
            token = db.get(AuthToken, message.auth_token_id)
            # No provider: nothing was minted at all.
            assert token.issued_at is None and token.issue_count == 0 and token.used_at is None
    # The operator's outbox screen shows the message, but nothing on it can be redeemed.
    op = login(platform, *make_operator())
    page = op.get("/operator/outbox")
    assert page.status_code == 200 and "tokensafe.example" in page.text
    assert not TOKEN_IN_TEXT.search(page.text)
    assert client_for(platform).get("/accept-invite?token=" + AUTH_LINK_PLACEHOLDER).status_code == 410
    assert client_for(platform).get("/reset?token=%7B%7BAUTH_LINK_TOKEN%7D%7D").status_code == 410


def test_live_token_passed_by_any_caller_is_stripped_and_killed(pg_database, platform):
    """Defense in depth: even code that builds a real link cannot get it stored."""
    from app.db import session_scope
    from app.models import AuthToken
    from app.security import digest, issue_token
    from app.services.email import AUTH_LINK_PLACEHOLDER, queue_email
    with session_scope() as db:
        raw = issue_token(db, purpose="reset", email="legacy-caller@tokensafe.example", ttl=timedelta(hours=1))
        db.flush()
        message = queue_email(db, kind="password_reset", to="legacy-caller@tokensafe.example", subject="Reset",
                              body=f"Reset here: https://app.example/reset?token={raw}\nBye")
        assert raw not in message.body and AUTH_LINK_PLACEHOLDER in message.body
        assert message.auth_token_id is not None
        assert db.scalar(select(AuthToken.id).where(AuthToken.token_hash == digest(raw))) is None
    assert client_for(platform).get("/reset?token=" + raw).status_code == 410
    # An unrelated token-shaped value is still stripped, and no token row is touched.
    with session_scope() as db:
        other = queue_email(db, kind="lead_notice", to="x@tokensafe.example", subject="s",
                            body="https://app.example/somewhere?a=1&token=" + "Z" * 40)
        assert "Z" * 40 not in other.body and other.auth_token_id is None


def test_link_exists_only_from_send_time_and_works_once(platform):
    from app.db import session_scope
    from app.models import AuthToken
    from app.services.email import deliver
    onboard_business(platform, business="Send Time Co", owner_email="owner@sendtime.example")
    _request_reset(platform, "owner@sendtime.example")
    sent: list[str] = []
    with session_scope() as db:
        message = _message_for(db, "owner@sendtime.example", "password_reset")
        deliver(db, message, _resend(_capture(sent)))
        assert message.status == "sent" and message.provider_message_id.startswith("msg_")
        token = db.get(AuthToken, message.auth_token_id)
        assert (token.issued_via, token.issue_count) == ("email", 1) and token.issued_at is not None
        assert token.expires_at - token.issued_at <= timedelta(minutes=46)       # reset TTL, from send time
        deliver(db, message, _resend(_capture(sent)))                           # idempotent
        assert len(sent) == 1
        raw = TOKEN_IN_TEXT.search(sent[0]).group(1)
        # The provider got a real link; the database still holds only the placeholder.
        assert raw not in message.body and raw not in _every_stored_string(db)
    visitor = client_for(platform)
    assert visitor.get("/reset?token=" + raw).status_code == 200
    assert visitor.post("/accept-invite", data={"token": raw, "name": "X", "password": PASSWORD}).status_code == 410  # wrong purpose
    assert visitor.post("/reset", data={"token": raw, "password": "a wholly new passphrase 1"}).status_code == 303
    assert visitor.post("/reset", data={"token": raw, "password": "a wholly new passphrase 2"}).status_code == 410  # single use
    assert visitor.get("/reset?token=" + raw).status_code == 410
    login(platform, "owner@sendtime.example", "a wholly new passphrase 1")


def test_failed_send_and_retry_never_leave_an_old_link_alive(platform):
    from app.db import session_scope
    from app.models import AuthToken
    from app.services.email import deliver
    onboard_business(platform, business="Retry Co", owner_email="owner@retry.example")
    _request_reset(platform, "owner@retry.example")
    first, second = [], []
    with session_scope() as db:
        message = _message_for(db, "owner@retry.example", "password_reset")
        deliver(db, message, _resend(_capture(first, status=500, payload={})))
        assert message.status == "failed"
        token = db.get(AuthToken, message.auth_token_id)
        assert token.issued_at is None                       # the link from the failed attempt was revoked
    failed_link = TOKEN_IN_TEXT.search(first[0]).group(1)
    assert client_for(platform).get("/reset?token=" + failed_link).status_code == 410
    with session_scope() as db:
        message = _message_for(db, "owner@retry.example", "password_reset")
        deliver(db, message, _resend(_capture(second)))
        assert message.status == "sent" and db.get(AuthToken, message.auth_token_id).issue_count == 2
    good_link = TOKEN_IN_TEXT.search(second[0]).group(1)
    assert good_link != failed_link
    assert client_for(platform).get("/reset?token=" + failed_link).status_code == 410
    assert client_for(platform).get("/reset?token=" + good_link).status_code == 200


def test_expiry_is_enforced_and_expired_messages_are_not_sent(platform):
    from app.db import session_scope
    from app.models import AuthToken, now
    from app.services.email import AuthLinkError, deliver, issue_link_manually
    onboard_business(platform, business="Expiry Co", owner_email="owner@expiry.example")
    _request_reset(platform, "owner@expiry.example")
    link = last_email_link("owner@expiry.example", "/reset")
    raw = link.split("token=")[1]
    assert client_for(platform).get(link).status_code == 200
    with session_scope() as db:
        message = _message_for(db, "owner@expiry.example", "password_reset")
        db.get(AuthToken, message.auth_token_id).expires_at = now() - timedelta(seconds=1)
    assert client_for(platform).get(link).status_code == 410
    assert client_for(platform).post("/reset", data={"token": raw, "password": "a wholly new passphrase 3"}).status_code == 410
    sent: list[str] = []
    with session_scope() as db:
        message = _message_for(db, "owner@expiry.example", "password_reset")
        message.status = "queued"
        deliver(db, message, _resend(_capture(sent)))
        assert message.status == "expired" and sent == []    # an expired request is never emailed
        with pytest.raises(AuthLinkError, match="expired"):
            issue_link_manually(db, message.id, operator_email=make_operator()[0])
    login(platform, "owner@expiry.example", PASSWORD)        # the password was never changed


def test_manual_issue_is_restricted_short_lived_recorded_and_rotating(platform):
    from app.db import session_scope
    from app.models import AuthToken
    from app.services.email import AuthLinkError, issue_link_manually
    owner, org_id, _ = onboard_business(platform, business="Manual Co", owner_email="owner@manual.example")
    owner.post("/app/team/invite", data={"csrf": csrf_of(owner), "email": "new@manual.example", "role": "staff"})
    operator_email, _ = make_operator()
    with session_scope() as db:
        message_id = _message_for(db, "new@manual.example", "invite").id
        notice_id = db.execute(text("SELECT id FROM email_outbox WHERE auth_token_id IS NULL LIMIT 1")).scalar()
    # Only an active platform operator may issue; a business owner or a made-up name may not.
    for who in ("owner@manual.example", "nobody@manual.example", ""):
        with pytest.raises(AuthLinkError, match="platform operator"):
            with session_scope() as db:
                issue_link_manually(db, message_id, operator_email=who)
    for bad in (uuid.uuid4(), "not-a-uuid", notice_id):
        if bad is None:
            continue
        with pytest.raises(AuthLinkError, match="not a pending"):
            with session_scope() as db:
                issue_link_manually(db, bad, operator_email=operator_email)
    with session_scope() as db:
        first = issue_link_manually(db, message_id, operator_email=operator_email)
    with session_scope() as db:
        second = issue_link_manually(db, message_id, operator_email=operator_email)
        message = _message_for(db, "new@manual.example", "invite")
        token = db.get(AuthToken, message.auth_token_id)
        assert (token.issued_via, token.issued_by, token.issue_count) == ("manual", operator_email, 2)
        assert timedelta(minutes=29) < token.expires_at - token.issued_at <= timedelta(minutes=30)
        assert message.status == "not_sent_no_provider"      # a manual link is not a delivery
        assert first.split("token=")[1] not in _every_stored_string(db)
    assert client_for(platform).get(first[first.index("/accept-invite"):]).status_code == 410   # rotated away
    joiner = client_for(platform)
    raw = second.split("token=")[1]
    # Role, organization and email come from the token row; form fields cannot change them.
    joined = joiner.post("/accept-invite", data={"token": raw, "name": "New Person", "password": PASSWORD,
                                                 "role": "owner", "org_id": str(uuid.uuid4()), "email": "attacker@evil.example"})
    assert joined.status_code == 303
    me = joiner.get("/api/v1/me").json()
    assert (me["user"]["email"], me["role"], me["organization"]["id"]) == ("new@manual.example", "staff", str(org_id))
    with pytest.raises(AuthLinkError, match="already been used"):
        with session_scope() as db:
            issue_link_manually(db, message_id, operator_email=operator_email)
    assert client_for(platform).post("/accept-invite", data={"token": raw, "name": "Thief", "password": PASSWORD}).status_code == 410


def test_cli_manual_issue_command(platform, capsys):
    from app import cli
    from app.db import session_scope
    onboard_business(platform, business="Cli Co", owner_email="owner@cli.example")
    _request_reset(platform, "owner@cli.example")
    operator_email, _ = make_operator()
    with session_scope() as db:
        message_id = str(_message_for(db, "owner@cli.example", "password_reset").id)
    assert cli.main(["cli", "issue-auth-link", message_id, "owner@cli.example"]) == 1
    refused = capsys.readouterr()
    assert "platform operator" in refused.err and "token=" not in refused.out + refused.err
    assert cli.main(["cli", "issue-auth-link", message_id, operator_email]) == 0
    link = capsys.readouterr().out.strip()
    assert client_for(platform).get(link[link.index("/reset"):]).status_code == 200


def test_owners_and_other_tenants_cannot_reach_tokens(platform):
    from app.db import session_scope
    a, org_a, _ = onboard_business(platform, business="Tenant A Tokens", owner_email="owner@tenant-a-tok.example")
    b, org_b, _ = onboard_business(platform, business="Tenant B Tokens", owner_email="owner@tenant-b-tok.example")
    b.post("/app/team/invite", data={"csrf": csrf_of(b), "email": "hire@tenant-b-tok.example", "role": "admin"})
    # No workspace route exposes the outbox or the token table.
    for path in ("/operator/outbox", "/operator"):
        assert a.get(path).status_code == 404
    export = a.get("/app/settings/export").text
    assert "token_hash" not in export and "hire@tenant-b-tok.example" not in export and not TOKEN_IN_TEXT.search(export)
    assert "auth_tokens" not in export and "email_outbox" not in export
    # An invitation to B only ever joins B, whoever redeems it.
    link = last_email_link("hire@tenant-b-tok.example", "/accept-invite")
    hire = client_for(platform)
    assert hire.post("/accept-invite", data={"token": link.split("token=")[1], "name": "Hire", "password": PASSWORD}).status_code == 303
    assert hire.get("/api/v1/me").json()["organization"]["id"] == str(org_b)
    with session_scope(org_id=org_a) as db:
        assert db.execute(text("SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id "
                               "WHERE u.email = 'hire@tenant-b-tok.example'")).scalar() == 0


def test_concurrent_redemption_succeeds_exactly_once(platform):
    onboard_business(platform, business="Race Co", owner_email="owner@race.example")
    _request_reset(platform, "owner@race.example")
    raw = last_email_link("owner@race.example", "/reset").split("token=")[1]
    results: list[int] = []
    barrier = threading.Barrier(6)

    def redeem(n: int) -> None:
        client = client_for(platform)
        barrier.wait()
        results.append(client.post("/reset", data={"token": raw, "password": f"racing passphrase number {n}"}).status_code)

    threads = [threading.Thread(target=redeem, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [303, 410, 410, 410, 410, 410], results


def test_cleanup_scrubs_used_and_expired_tokens(platform):
    from app.db import session_scope
    from app.models import AuthToken, EmailMessage, now
    from app.services.email import cleanup_auth_artifacts
    onboard_business(platform, business="Cleanup Co", owner_email="owner@cleanup.example")
    _request_reset(platform, "owner@cleanup.example")
    raw = last_email_link("owner@cleanup.example", "/reset").split("token=")[1]
    with session_scope() as db:
        message = _message_for(db, "owner@cleanup.example", "password_reset")
        token_id = message.auth_token_id
        db.get(AuthToken, token_id).expires_at = now() - timedelta(minutes=1)
    with session_scope() as db:
        result = cleanup_auth_artifacts(db)
        assert result["token_hashes_scrubbed"] >= 1
        token = db.get(AuthToken, token_id)
        from app.security import digest
        assert token.token_hash != digest(raw) and token.issued_at is None
        token.expires_at = now() - timedelta(days=8)
        db.get(EmailMessage, message.id).created_at = now() - timedelta(days=8)
    with session_scope() as db:
        result = cleanup_auth_artifacts(db)
        assert result["tokens_deleted"] >= 1 and result["bodies_blanked"] >= 1
        assert db.get(AuthToken, token_id) is None
        aged = db.get(EmailMessage, message.id)
        assert aged.body == "" and aged.auth_token_id is None     # FK cleared, row kept for the delivery record
    # The cleanup job is registered and runs through the queue.
    from app.services import jobs
    assert "auth_cleanup" in jobs.HANDLERS


# ---------------------------------------------------------------- review round 2 (GPT-6)

def _live_reset_token(db, email: str) -> str:
    from app.security import issue_token
    raw = issue_token(db, purpose="reset", email=email, ttl=timedelta(hours=1))
    db.flush()
    return raw


def test_token_in_subject_is_scrubbed_and_revoked(pg_database, platform):
    """GPT-6 review finding: a generic caller could persist a usable token in the SUBJECT."""
    from app.db import session_scope
    from app.models import AuthToken
    from app.security import digest
    from app.services.email import AUTH_LINK_PLACEHOLDER, queue_email
    with session_scope() as db:
        raw = _live_reset_token(db, "subject-caller@tokensafe.example")
        message = queue_email(db, kind="password_reset", to="subject-caller@tokensafe.example",
                              subject=f"Reset now: https://app.example/reset?token={raw}",
                              body="Open the link in the subject line.")
        assert raw not in message.subject and AUTH_LINK_PLACEHOLDER in message.subject
        assert message.auth_token_id is not None                      # bound, so a fresh link is minted at send
        assert db.scalar(select(AuthToken.id).where(AuthToken.token_hash == digest(raw))) is None
        assert raw not in _every_stored_string(db)
    assert client_for(platform).get("/reset?token=" + raw).status_code == 410
    # The 200-character truncation must not be able to cut a token in half and hide it.
    with session_scope() as db:
        raw = _live_reset_token(db, "subject-long@tokensafe.example")
        message = queue_email(db, kind="password_reset", to="subject-long@tokensafe.example",
                              subject="x" * 170 + " token=" + raw, body="b")
        assert raw[:20] not in message.subject and len(message.subject) <= 200
    assert client_for(platform).get("/reset?token=" + raw).status_code == 410


@pytest.mark.parametrize("template", [
    "Your reset code is {t} (valid one hour)",                 # bare token, no token= at all
    "https://app.example/reset/{t}",                           # path segment
    "https://app.example/reset#token={t}",                     # fragment
    "https://app.example/reset?token%3D{t}",                   # percent-encoded separator
    "https://app.example/reset?next=/app&amp;token={t}",       # HTML-escaped ampersand
    "TOKEN={t}",                                               # different case
    "<a href='https://app.example/r?t={t}'>reset</a>",         # different parameter name
])
def test_a_live_token_is_caught_wherever_and_however_it_appears(pg_database, platform, template):
    """Screening must not depend on the link's format: any stored text that IS a live token
    is removed and the token revoked."""
    from app.db import session_scope
    from app.services.email import queue_email
    for field in ("subject", "body"):
        with session_scope() as db:
            raw = _live_reset_token(db, "format@tokensafe.example")
            text_ = template.format(t=raw)
            message = queue_email(db, kind="notice", to="format@tokensafe.example",
                                  subject=text_ if field == "subject" else "s",
                                  body=text_ if field == "body" else "b")
            assert raw not in message.subject + message.body, (field, template)
        assert client_for(platform).get("/reset?token=" + raw).status_code == 410, (field, template)


def test_provider_error_text_cannot_persist_the_token(platform):
    """A provider (or proxy) that echoes the request in its error must not get the link stored."""
    from app.db import session_scope
    from app.models import Job
    from app.services.email import EmailProvider, SendResult, deliver
    onboard_business(platform, business="Echo Co", owner_email="owner@echo.example")
    _request_reset(platform, "owner@echo.example")
    seen: list[str] = []

    class EchoingProvider(EmailProvider):
        name = "echo"

        def send(self, *, to, subject, body):
            seen.append(body)
            return SendResult(False, error="rejected payload: " + body)

    with session_scope() as db:
        message = _message_for(db, "owner@echo.example", "password_reset")
        deliver(db, message, EchoingProvider())
        raw = TOKEN_IN_TEXT.search(seen[0]).group(1)
        assert message.status == "failed" and raw not in (message.error or "")
        assert raw not in _every_stored_string(db)
        assert db.scalar(select(Job.id).where(Job.last_error.contains(raw))) is None
    assert client_for(platform).get("/reset?token=" + raw).status_code == 410


def test_concurrent_delivery_sends_one_email_with_one_working_link(platform, monkeypatch):
    """Two workers picking up the same message (a reclaimed job) must not both send."""
    import time as _time
    from app.db import session_scope
    from app.models import Job
    from app.services import email as email_service
    from app.services import jobs
    onboard_business(platform, business="Twice Co", owner_email="owner@twice.example")
    _request_reset(platform, "owner@twice.example")
    sent: list[str] = []

    def slow(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["text"])
        _time.sleep(0.4)
        return httpx.Response(200, json={"id": "msg_" + uuid.uuid4().hex})

    monkeypatch.setattr(email_service, "get_provider", lambda settings=None: _resend(slow))
    with session_scope() as db:
        message_id = str(_message_for(db, "owner@twice.example", "password_reset").id)
    job = Job(kind="send_email", payload={"message_id": message_id}, idempotency_key="x", attempts=1)
    barrier, errors = threading.Barrier(2), []

    def work() -> None:
        barrier.wait()
        try:
            jobs.HANDLERS["send_email"](job)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(sent) == 1, (errors, len(sent))
    raw = TOKEN_IN_TEXT.search(sent[0]).group(1)
    assert client_for(platform).get("/reset?token=" + raw).status_code == 200
