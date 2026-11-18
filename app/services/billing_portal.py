"""Owner-only Stripe Billing Portal integration for verified, linked subscriptions.

A checkout session or a text email address is NOT proof of account ownership.
BillingSubscription and ServiceOrder are intentionally PLATFORM tables without
PostgreSQL tenant RLS. The exact DOUBLE org_id WHERE predicate below is therefore
the load-bearing tenant boundary, and callers MUST supply the verified current
Principal.org.id, never a client-supplied tenant identifier. Resolve the Stripe
Customer only from signed-event subscription facts paired to a paid ServiceOrder.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from ..db import TenantSession
from ..models import BillingSubscription, ServiceOrder

_CUSTOMER = re.compile(r"cus_[A-Za-z0-9]{8,200}\Z")
_STRIPE_HOST = "billing.stripe.com"


class BillingPortalError(RuntimeError):
    pass


def verified_customer_for(db: TenantSession, org_id) -> str | None:
    """Never accept user/client-supplied Stripe identifiers or a profile snapshot."""
    # PLATFORM tables lack RLS: both org predicates are mandatory tenant isolation.
    # The route passes only p.org.id derived from the authenticated membership.
    rows = db.execute(
        select(BillingSubscription, ServiceOrder)
        .join(ServiceOrder, BillingSubscription.order_id == ServiceOrder.id)
        .where(BillingSubscription.org_id == org_id, ServiceOrder.org_id == org_id)
    ).all()
    customers: set[str] = set()
    for subscription, order in rows:
        customer = subscription.stripe_customer_id
        if (not isinstance(customer, str) or not _CUSTOMER.fullmatch(customer)
                or order.payment_provider != "stripe"
                or subscription.status in ("canceled", "incomplete_expired")
                or subscription.invoice_status == "refunded"
                or not (order.payment_evidence or "").startswith("stripe:")
                or order.status not in ("paid", "provisioned")
                or order.paid_at is None
                or order.offer_snapshot.get("billing") != "monthly"):
            continue
        customers.add(customer)
    # Multiple independent Stripe customer records on one tenant are ambiguous:
    # never guess whose billing access to grant.
    return next(iter(customers)) if len(customers) == 1 else None


def create_portal_session(customer_id: str, *, return_url: str,
                          stripe_secret_key: str, client: httpx.Client | None = None) -> str:
    """Make a Stripe-hosted session; never expose credential or customer ID to browser."""
    if (not isinstance(customer_id, str) or not _CUSTOMER.fullmatch(customer_id)
            or not stripe_secret_key.startswith(("sk_live_", "sk_test_"))):
        raise BillingPortalError("Billing management is not configured.")
    try:
        target = urlsplit(return_url)
        target_port = target.port
    except ValueError:
        raise BillingPortalError("Invalid billing return address.") from None
    if (target.scheme != "https" or target.username is not None or target.password is not None
            or target.query or target.fragment or target.path != "/app/settings"
            or not target.hostname or target_port not in (None, 443)):
        raise BillingPortalError("Invalid billing return address.")

    def call(http: httpx.Client):
        try:
            response = http.post("https://api.stripe.com/v1/billing_portal/sessions",
                                 auth=(stripe_secret_key, ""),
                                 data={"customer": customer_id, "return_url": return_url},
                                 headers={"User-Agent": "MeasuredField/1.0"})
        except httpx.HTTPError:
            raise BillingPortalError("Billing provider is currently unavailable.") from None
        if response.status_code != 200:
            raise BillingPortalError("Billing provider did not open the portal.")
        try:
            body = response.json()
        except (ValueError, TypeError):
            raise BillingPortalError("Invalid billing provider response.") from None
        url = body.get("url") if isinstance(body, dict) else None
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise BillingPortalError("Invalid billing provider response.")
        try:
            parts = urlsplit(url)
            portal_port = parts.port
        except ValueError:
            raise BillingPortalError("Invalid billing provider redirect.") from None
        if (parts.scheme != "https" or parts.hostname != _STRIPE_HOST
                or parts.username is not None or parts.password is not None
                or portal_port not in (None, 443)
                or parts.query or parts.fragment
                or not parts.path.startswith("/p/session/")
                or not parts.path[len("/p/session/"):].strip("/")
                or any(ord(c) <= 31 or ord(c) == 127 for c in url)):
            raise BillingPortalError("Invalid billing provider redirect.")
        return url

    if client is not None:
        return call(client)
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as http:
        return call(http)
