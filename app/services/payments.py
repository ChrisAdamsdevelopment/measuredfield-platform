"""Payments through a provider interface.

An order becomes ``paid`` in exactly two ways, both leaving evidence:

* a Stripe webhook whose signature verifies against the configured secret, or
* a platform operator recording an out-of-band payment with a reference.

Nothing else marks an order paid. With no provider configured the customer's approval is
recorded as ``approved_unpaid`` and the page states that no payment was taken.

The Stripe adapter talks to the REST API directly with httpx (no SDK). It is covered by
tests against a mock transport and by signature tests; it has NOT been exercised against
Stripe's live or test-mode API from this workbench.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx

from ..config import Settings, get_settings


class PaymentError(RuntimeError):
    pass


class StripeProvider:
    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str, client: httpx.Client | None = None):
        self.secret_key, self.webhook_secret = secret_key, webhook_secret
        self.client = client or httpx.Client(timeout=15)

    def create_checkout(self, *, order_id: str, offer: dict, customer_email: str,
                        success_url: str, cancel_url: str) -> dict:
        recurring = offer.get("billing") == "monthly"
        form = {
            "mode": "subscription" if recurring else "payment",
            "client_reference_id": order_id,
            "customer_email": customer_email,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata[order_id]": order_id,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(int(offer["price_cents"])),
            "line_items[0][price_data][product_data][name]": offer["name"],
        }
        if recurring:
            form["line_items[0][price_data][recurring][interval]"] = "month"
        try:
            response = self.client.post(
                "https://api.stripe.com/v1/checkout/sessions", data=form,
                auth=(self.secret_key, ""), headers={"Idempotency-Key": "mf-order-" + order_id})
        except httpx.HTTPError as exc:
            raise PaymentError("Payment provider unreachable") from exc
        if response.status_code >= 300:
            raise PaymentError(f"Payment provider rejected the request (HTTP {response.status_code})")
        body = response.json()
        if not body.get("id") or not body.get("url"):
            raise PaymentError("Payment provider returned an incomplete session")
        return {"id": body["id"], "url": body["url"]}

    def verify_webhook(self, payload: bytes, signature_header: str, *, tolerance: int = 300,
                       at: float | None = None) -> dict:
        """Verify Stripe's ``t=...,v1=...`` signature scheme and return the event."""
        parts = dict(p.split("=", 1) for p in (signature_header or "").split(",") if "=" in p)
        timestamp, candidates = parts.get("t", ""), [
            p.split("=", 1)[1] for p in (signature_header or "").split(",") if p.startswith("v1=")]
        if not timestamp.isdigit() or not candidates:
            raise PaymentError("Missing signature")
        if abs((at if at is not None else time.time()) - int(timestamp)) > tolerance:
            raise PaymentError("Signature timestamp outside tolerance")
        expected = hmac.new(self.webhook_secret.encode(), timestamp.encode() + b"." + payload,
                            hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, c) for c in candidates):
            raise PaymentError("Signature mismatch")
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PaymentError("Malformed event") from exc
        if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
            raise PaymentError("Malformed event")
        return event


def get_provider(settings: Settings | None = None) -> StripeProvider | None:
    settings = settings or get_settings()
    if settings.payment_provider == "stripe" and settings.stripe_secret_key and settings.stripe_webhook_secret:
        return StripeProvider(settings.stripe_secret_key, settings.stripe_webhook_secret)
    return None


def sign_for_test(secret: str, payload: bytes, timestamp: int) -> str:
    """Build a signature header the way Stripe does. Used only by tests."""
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"
