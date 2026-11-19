"""MF-075: official Census tool contracts, fake provider, no real credentials."""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.mcp_connector.gateway import ConnectorAccessDenied
from app.mcp_plugin import census_market_tools as tools
from app.routers import census_market as market
from app.services.census_market import CensusMarketClient, CensusUnavailable
from .test_census_market import fake_matrix


class FakeGateway:
    def __init__(self, *, allow=True):
        self.allow = allow
        self.calls = []
        self.user_id = uuid.uuid4()

    def overview_for(self, identity, business_id):
        self.calls.append(("overview", identity, business_id))
        if not self.allow:
            raise ConnectorAccessDenied("Business is unavailable")
        return {"business_id": business_id}

    def _user(self, identity):
        self.calls.append(("_user", identity))
        if not self.allow:
            raise ConnectorAccessDenied("OAuth identity is not authorized")
        return self.user_id

    def _active_user(self, db, user_id):
        self.calls.append(("_active_user", user_id))
        if not self.allow:
            raise ConnectorAccessDenied("Account is unavailable")

    def _enter_business(self, db, user_id, org_id):
        self.calls.append(("_enter_business", org_id))
        if not self.allow:
            raise ConnectorAccessDenied("Business is unavailable")
        return SimpleNamespace(id=org_id)


class FakeSession:
    def __init__(self):
        self.events = []

    def set_context(self, **kwargs):
        self.events.append(("context", kwargs))

    def commit(self):
        self.events.append(("commit", None))

    def rollback(self):
        self.events.append(("rollback", None))

    def close(self):
        self.events.append(("close", None))


@pytest.fixture
def synthetic(monkeypatch):
    monkeypatch.setattr(market, "_CENSUS", CensusMarketClient(
        transport=fake_matrix, key_loader=lambda: "A" * 40, max_calls=30,
    ))
    return SimpleNamespace(
        identity=object(), business_id=str(uuid.uuid4()), gateway=FakeGateway(),
    )


def test_list_markets_checks_tenant_permission_first(synthetic):
    result = tools.available_markets(
        identity=synthetic.identity, business_id=synthetic.business_id,
        gateway=synthetic.gateway,
    )
    assert {x["id"] for x in result["markets"]} == set(market.AREAS)
    assert synthetic.gateway.calls[0][0] == "overview"
    assert result["coverage"] == "Prescott-region pilot only"
    assert "not current named competitors" in result["note"]


def test_list_markets_rejects_unauthorized_business(synthetic):
    synthetic.gateway.allow = False
    with pytest.raises(ConnectorAccessDenied):
        tools.available_markets(identity=synthetic.identity,
            business_id=synthetic.business_id, gateway=synthetic.gateway)


def test_snapshot_six_public_observations_with_source_years(synthetic, monkeypatch):
    quotas = []
    monkeypatch.setattr(tools, "_consume_owner_quota", lambda **kw: quotas.append(kw))
    result = tools.market_snapshot(identity=synthetic.identity,
        business_id=synthetic.business_id, area_slug="prescott-valley",
        gateway=synthetic.gateway)
    assert len(quotas) == 1
    assert len(result["source_records"]) == 6
    assert result["evidence_type"] == "official_public_aggregate"
    assert result["selected_market"]["business_zip"] == "86314"
    assert result["partial_result"] is False
    source_text = json.dumps(result)
    assert "2023" in source_text and "2024" in source_text
    assert "971" in source_text and "2618" in source_text
    assert "thousand_usd" in source_text
    assert "business_zip" in source_text and "acs_zcta_not_postal_zip" in source_text
    assert "key=" not in source_text.lower()
    assert "A" * 40 not in source_text
    assert "not current named competitors" in result["note"]


def test_invalid_slug_rejected_without_any_query(synthetic, monkeypatch):
    monkeypatch.setattr(tools, "_consume_owner_quota",
                        lambda **kw: pytest.fail("invalid area consumed quota"))
    with pytest.raises(ValueError):
        tools.market_snapshot(identity=synthetic.identity,
            business_id=synthetic.business_id, area_slug="../private/key",
            gateway=synthetic.gateway)


def test_partial_census_source_failure_is_not_zero(synthetic, monkeypatch):
    def partial(endpoint, params, key):
        if endpoint == "/data/2024/nonemp":
            raise CensusUnavailable("fake upstream outage")
        return fake_matrix(endpoint, params, key)
    monkeypatch.setattr(market, "_CENSUS", CensusMarketClient(
        transport=partial, key_loader=lambda: "A" * 40, max_calls=30,
    ))
    monkeypatch.setattr(tools, "_consume_owner_quota", lambda **kw: None)
    result = tools.market_snapshot(identity=synthetic.identity,
        business_id=synthetic.business_id, area_slug="prescott",
        gateway=synthetic.gateway)
    assert result["partial_result"] is True
    assert "county_nonemployers" in result["unavailable_sources"]
    missing = next(x for x in result["source_records"]
                   if x["source_id"] == "county_nonemployers")
    assert missing["availability"] == "not_loaded"
    assert missing["metrics"] == []


def test_quota_requires_active_user_tenant_and_commits_only_accounting(synthetic, monkeypatch):
    db = FakeSession()
    monkeypatch.setattr(tools, "new_session", lambda: db)
    limits = []
    def rate(_db, key, **kwargs):
        limits.append((key, kwargs))
        return False
    monkeypatch.setattr(tools, "rate_limited", rate)
    tools._consume_owner_quota(identity=synthetic.identity,
        business_id=synthetic.business_id, gateway=synthetic.gateway)
    assert [c[0] for c in synthetic.gateway.calls] == [
        "_user", "_active_user", "_enter_business"]
    assert len(limits) == 2
    assert all(x[1]["window_seconds"] == 3600 for x in limits)
    assert ("commit", None) in db.events and ("close", None) in db.events


def test_rejected_identity_never_calls_rate_or_provider(synthetic, monkeypatch):
    synthetic.gateway.allow = False
    monkeypatch.setattr(tools, "new_session",
                        lambda: pytest.fail("unverified identity acquired DB"))
    with pytest.raises(ConnectorAccessDenied):
        tools._consume_owner_quota(identity=synthetic.identity,
            business_id=synthetic.business_id, gateway=synthetic.gateway)


def test_quota_exceeded_denies_without_census_request(synthetic, monkeypatch):
    db = FakeSession()
    monkeypatch.setattr(tools, "new_session", lambda: db)
    monkeypatch.setattr(tools, "rate_limited",
                        lambda _db, key, **kw: "mcp-census-user" in key)
    with pytest.raises(ConnectorAccessDenied, match="Hourly"):
        tools._consume_owner_quota(identity=synthetic.identity,
            business_id=synthetic.business_id, gateway=synthetic.gateway)
    assert ("commit", None) in db.events
    assert ("close", None) in db.events


def test_bad_business_id_rejected_before_opening_database(synthetic, monkeypatch):
    monkeypatch.setattr(tools, "new_session",
                        lambda: pytest.fail("bad id opened DB"))
    with pytest.raises(ConnectorAccessDenied):
        tools._consume_owner_quota(identity=synthetic.identity,
            business_id="not-a-uuid", gateway=synthetic.gateway)


def test_mcp_market_tools_are_registered_with_auth_metadata():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "app/mcp_plugin/server.py").read_text(encoding="utf-8")
    for name in ("list_supported_census_markets", "get_census_market_snapshot"):
        assert "def " + name + "(" in source
    assert source.count("meta=oauth_tool_metadata(settings.read_scope)") >= 7
