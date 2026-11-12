"""Outbound email through a provider interface.

A message is only ever marked ``sent`` when a provider accepted it and returned an id.
With no provider configured the message is stored as ``not_sent_no_provider`` and the UI
says so; nothing pretends to have been delivered.

Sign-in links (invitations, password resets) are handled so that no usable link is ever
written to the database:

* The stored body contains ``AUTH_LINK_PLACEHOLDER`` where the secret belongs.
* The real token is minted inside ``deliver`` at the moment a provider is about to send. It
  exists only in memory, and its SHA-256 replaces whatever hash the token row held, so any
  earlier link for the same invitation or reset stops working.
* If the provider reports a failure, that token is revoked again; a retry mints a new one.
* With no provider, nothing is minted. The manual procedure (``issue_link_manually``, run
  from the server CLI) creates a short-lived link, shows it once, and records who issued it.
* ``queue_email`` refuses to store a live token from ANY caller: it strips token values
  from the body and kills the tokens they belonged to.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import or_, select

from ..config import Settings, get_settings
from ..models import AuthToken, EmailMessage, User, now
from ..security import digest, new_token, normalize_email


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None


class EmailProvider:
    name = "none"

    def send(self, *, to: str, subject: str, body: str) -> SendResult:  # pragma: no cover - interface
        raise NotImplementedError


class ResendProvider(EmailProvider):
    """https://resend.com/docs/api-reference/emails/send-email  (untested against the live API)."""
    name = "resend"

    def __init__(self, api_key: str, sender: str, client: httpx.Client | None = None):
        self.api_key, self.sender = api_key, sender
        self.client = client or httpx.Client(timeout=10)

    def send(self, *, to: str, subject: str, body: str) -> SendResult:
        try:
            response = self.client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": "Bearer " + self.api_key},
                json={"from": self.sender, "to": [to], "subject": subject, "text": body})
        except httpx.HTTPError as exc:
            return SendResult(False, error=type(exc).__name__)
        if response.status_code >= 300:
            return SendResult(False, error=f"HTTP {response.status_code}")
        message_id = (response.json() or {}).get("id")
        if not message_id:
            return SendResult(False, error="Provider returned no message id")
        return SendResult(True, provider_message_id=str(message_id))


def get_provider(settings: Settings | None = None) -> EmailProvider | None:
    settings = settings or get_settings()
    if settings.email_provider == "resend" and settings.resend_api_key and settings.email_from:
        return ResendProvider(settings.resend_api_key, settings.email_from)
    return None


AUTH_LINK_PLACEHOLDER = "{{AUTH_LINK_TOKEN}}"
AUTH_KINDS = {"invite": ("invite", "/accept-invite"), "password_reset": ("reset", "/reset")}
MANUAL_LINK_MINUTES = 30
# How long an invitation or reset may wait to be SENT. The link's own lifetime starts at send.
PENDING_WINDOW = {"invite": timedelta(days=14), "reset": timedelta(hours=1)}
_TOKEN_PARAM = re.compile(r"([?&]token=)([A-Za-z0-9_\-]{16,})")


class AuthLinkError(ValueError):
    pass


def _link_ttl(purpose: str, settings: Settings) -> timedelta:
    if purpose == "invite":
        return timedelta(hours=settings.invite_ttl_hours)
    return timedelta(minutes=settings.reset_ttl_minutes)


def _unusable_hash() -> str:
    """Hash of a random value that is discarded immediately: matches no link anyone holds."""
    return digest(new_token())


def queue_email(db, *, kind: str, to: str, subject: str, body: str, org_id=None) -> EmailMessage:
    """Store a message for delivery. Token values never survive this function.

    Any ``token=<value>`` in the body is replaced by the placeholder. If the value belonged
    to a live auth token, that token is bound to this message and its hash is replaced with
    an unusable one, so the link the caller built cannot be redeemed by anyone who later
    reads the outbox, a log or a backup. A fresh link is minted at send time.
    """
    auth_token_id = None
    for match in _TOKEN_PARAM.finditer(body):
        row = db.scalar(select(AuthToken).where(AuthToken.token_hash == digest(match.group(2))).with_for_update())
        if row is not None:
            row.token_hash, row.issued_at, row.issued_via = _unusable_hash(), None, None
            if row.used_at is None and auth_token_id is None:
                auth_token_id = row.id
                row.expires_at = now() + PENDING_WINDOW.get(row.purpose, timedelta(hours=1))
    body = _TOKEN_PARAM.sub(lambda m: m.group(1) + AUTH_LINK_PLACEHOLDER, body)
    message = EmailMessage(kind=kind, to_email=to, subject=subject[:200], body=body, org_id=org_id,
                           auth_token_id=auth_token_id)
    db.add(message)
    db.flush()
    return message


def queue_auth_email(db, *, kind: str, to: str, subject: str, intro: str, outro: str = "",
                     org_id=None, role: str | None = None) -> EmailMessage:
    """Queue an invitation or password-reset message without ever creating a usable link."""
    purpose, path = AUTH_KINDS[kind]
    settings = get_settings()
    token = AuthToken(token_hash=_unusable_hash(), purpose=purpose, email=normalize_email(to),
                      org_id=org_id, role=role, expires_at=now() + PENDING_WINDOW[purpose])
    db.add(token)
    db.flush()
    link = f"{settings.public_base_url}{path}?token={AUTH_LINK_PLACEHOLDER}"
    message = EmailMessage(kind=kind, to_email=normalize_email(to), subject=subject[:200], org_id=org_id,
                           body=f"{intro}\n{link}\n\n{outro}".rstrip() + "\n", auth_token_id=token.id)
    db.add(message)
    db.flush()
    return message


def _mint(db, token: AuthToken, *, via: str, ttl: timedelta, issued_by: str | None = None) -> str:
    """Create the one usable token for this row, replacing any earlier one."""
    raw = new_token()
    token.token_hash, token.issued_at, token.issued_via = digest(raw), now(), via
    token.issued_by, token.issue_count = issued_by, (token.issue_count or 0) + 1
    token.expires_at = now() + ttl
    db.flush()
    return raw


def _revoke_link(token: AuthToken) -> None:
    token.token_hash, token.issued_at, token.issued_via = _unusable_hash(), None, None


def _locked_token(db, message: EmailMessage) -> AuthToken | None:
    return db.scalar(select(AuthToken).where(AuthToken.id == message.auth_token_id).with_for_update())


def deliver(db, message: EmailMessage, provider: EmailProvider | None = None) -> EmailMessage:
    """Attempt delivery once. Idempotent: an already-sent message is left alone."""
    if message.status in ("sent", "expired"):
        return message
    provider = provider if provider is not None else get_provider()
    body, token = message.body, None
    if message.auth_token_id is not None or AUTH_LINK_PLACEHOLDER in body:
        token = _locked_token(db, message) if message.auth_token_id else None
        if token is None or token.used_at is not None or token.expires_at <= now():
            message.status = "expired"
            message.error = "The sign-in request this message belonged to is no longer valid."
            return message
    if provider is None:
        message.status = "not_sent_no_provider"   # for sign-in messages: nothing was minted
        message.provider = "none"
        return message
    if token is not None:
        raw = _mint(db, token, via="email", ttl=_link_ttl(token.purpose, get_settings()))
        body = body.replace(AUTH_LINK_PLACEHOLDER, raw)
    result = provider.send(to=message.to_email, subject=message.subject, body=body)
    message.provider = provider.name
    if result.ok:
        message.status, message.provider_message_id, message.sent_at = "sent", result.provider_message_id, now()
        message.error = None
    else:
        message.status, message.error = "failed", (result.error or "unknown")[:300]
        if token is not None:
            # The provider did not confirm it has the message: do not leave that link live.
            _revoke_link(token)
            token.expires_at = now() + PENDING_WINDOW.get(token.purpose, timedelta(hours=1))
    return message


def issue_link_manually(db, message_id, *, operator_email: str) -> str:
    """Manual recovery when no email provider can deliver a sign-in message.

    Mints a link valid for MANUAL_LINK_MINUTES, returns it exactly once and stores only its
    hash. Any link issued earlier for the same request stops working. The operator must be
    an active platform administrator; who issued it, when and how often is kept on the
    token row. The caller is responsible for getting the link to the right person.
    """
    operator_email = normalize_email(operator_email)
    operator = db.scalar(select(User).where(User.email == operator_email, User.is_platform_admin.is_(True),
                                            User.disabled_at.is_(None)))
    if operator is None:
        raise AuthLinkError("Only an active platform operator can issue a sign-in link.")
    try:
        message = db.get(EmailMessage, uuid.UUID(str(message_id)))
    except ValueError:
        message = None
    if message is None or message.auth_token_id is None or message.kind not in AUTH_KINDS:
        raise AuthLinkError("That message is not a pending invitation or password reset.")
    token = _locked_token(db, message)
    if token is None or token.used_at is not None:
        raise AuthLinkError("That invitation or reset has already been used.")
    if token.expires_at <= now():
        raise AuthLinkError("That invitation or reset has expired. Ask for a new one.")
    raw = _mint(db, token, via="manual", ttl=timedelta(minutes=MANUAL_LINK_MINUTES), issued_by=operator_email)
    # The message's delivery status is left alone: a manual link is not a delivery. The
    # issue itself is recorded on the token row (issued_via, issued_by, issued_at, issue_count).
    return f"{get_settings().public_base_url}{AUTH_KINDS[message.kind][1]}?token={raw}"


def cleanup_auth_artifacts(db) -> dict:
    """Post-use and post-expiry cleanup. Safe to run repeatedly.

    * Used or expired tokens have their hash replaced, so a database copy taken later holds
      nothing that ever matched a link.
    * Token rows are deleted a week after use or expiry (kept that long for support).
    * Sign-in messages older than a week lose their body text.
    """
    current = now()
    week_ago = current - timedelta(days=7)
    scrubbed = deleted = blanked = 0
    for token in db.scalars(select(AuthToken).where(
            AuthToken.issued_at.is_not(None), or_(AuthToken.used_at.is_not(None), AuthToken.expires_at <= current))):
        _revoke_link(token)
        scrubbed += 1
    db.flush()
    for token in db.scalars(select(AuthToken).where(
            or_(AuthToken.used_at < week_ago, AuthToken.expires_at < week_ago))):
        db.delete(token)
        deleted += 1
    for message in db.scalars(select(EmailMessage).where(
            EmailMessage.kind.in_(tuple(AUTH_KINDS)), EmailMessage.created_at < week_ago, EmailMessage.body != "")):
        message.body = ""
        blanked += 1
    return {"token_hashes_scrubbed": scrubbed, "tokens_deleted": deleted, "bodies_blanked": blanked}
