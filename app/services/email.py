"""Outbound email through a provider interface.

A message is only ever marked ``sent`` when a provider accepted it and returned an id.
With no provider configured the message is stored as ``not_sent_no_provider`` and the UI
says so; nothing pretends to have been delivered.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import Settings, get_settings
from ..models import EmailMessage, now


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


def queue_email(db, *, kind: str, to: str, subject: str, body: str, org_id=None) -> EmailMessage:
    message = EmailMessage(kind=kind, to_email=to, subject=subject[:200], body=body, org_id=org_id)
    db.add(message)
    db.flush()
    return message


def deliver(db, message: EmailMessage, provider: EmailProvider | None = None) -> EmailMessage:
    """Attempt delivery once. Idempotent: an already-sent message is left alone."""
    if message.status == "sent":
        return message
    provider = provider if provider is not None else get_provider()
    if provider is None:
        message.status = "not_sent_no_provider"
        message.provider = "none"
        return message
    result = provider.send(to=message.to_email, subject=message.subject, body=message.body)
    message.provider = provider.name
    if result.ok:
        message.status, message.provider_message_id, message.sent_at = "sent", result.provider_message_id, now()
        message.error = None
    else:
        message.status, message.error = "failed", (result.error or "unknown")[:300]
    return message
