"""Founding promo UI is visible only when a real Price and disclosure exist."""
from __future__ import annotations
import json
import uuid

from app.config import get_settings
from .conftest import client_for


def _assessment_result(platform):
    visitor = client_for(platform)
    email = f"promo-{uuid.uuid4().hex[:12]}@example.test"
    result = visitor.post("/assessment", data={
        "excited": "I want my small company website to look professional",
        "blocker": "Need to follow up with leads and see measurable progress.",
        "business_name": "Promo Preview Company", "contact_name": "A Real Owner",
        "email": email, "industry": "other", "consent": "yes",
    })
    assert result.status_code == 303
    return visitor, result.headers["location"]


def test_promocode_hidden_until_configured_and_then_copyable(platform, monkeypatch):
    from app.services import payments
    client, url = _assessment_result(platform)
    monkeypatch.setattr(payments, "get_provider", lambda settings=None: object())
    original = get_settings().offers_json
    base_offer = {
        "code": "growth_partner", "name": "Growth Partner",
        "price_cents": 9900, "billing": "monthly",
        "summary": "Optional hands-on founder support.",
        "includes": ["An agreed amount of personal help each month"],
        "stripe_price_id": "price_FictionalTest99",
        "allow_promotion_codes": True,
        "promotion_code": "GUIDEDLAUNCH",
        "promotion_disclosure": (
            "First month of Growth Partner free; then $99/month unless cancelled. "
            "Core Workspace is billed separately."),
    }
    settings = get_settings()
    try:
        settings.offers_json = ""  # default: no advertised promotion
        plain = client.get(url)
        assert plain.status_code == 200
        assert "data-copy-promo=" not in plain.text
        assert "promo-copy.js" in plain.text

        settings.offers_json = json.dumps([base_offer])
        active = client.get(url)
        assert active.status_code == 200, active.text[:500]
        assert 'data-copy-promo="GUIDEDLAUNCH"' in active.text
        assert "First month of Growth Partner free" in active.text
        assert "Core Workspace is billed separately" in active.text

        settings.offers_json = json.dumps([{**base_offer, "promotion_disclosure": ""}])
        assert "data-copy-promo=" not in client.get(url).text
        settings.offers_json = json.dumps([{**base_offer, "stripe_price_id": ""}])
        assert "data-copy-promo=" not in client.get(url).text
    finally:
        settings.offers_json = original
