"""MF-074: real PostgreSQL authenticated market screen, fake public Census backend.

No credentials, network, production customers or actual Census calls are made by
these tests. The provider is replaced at the router boundary.
"""
from __future__ import annotations

import json

from app.routers import census_market as market
from app.services.census_market import (
    CensusMarketClient, CensusQuery, CensusUnavailable,
)
from .conftest import client_for, csrf_of, onboard_business
from .test_census_market import fake_matrix

FAKE_KEY = "A" * 40


def _fake_service(*, fail_products=()):
    def transport(endpoint, params, key):
        if (endpoint == "/data/2024/nonemp" and "nonemp_county" in fail_products
            or endpoint == "/data/2023/cbp" and params.get("NAICS2017") == "236118"
            and "county_remodelers" in fail_products):
            raise CensusUnavailable("Synthetic upstream failure")
        if endpoint == "/data/2023/cbp" and params.get("NAICS2017") == "236118":
            assert key == FAKE_KEY
            return json.dumps([
                ["NAME", "ESTAB", "EMP", "PAYANN", "state", "county"],
                ["Yavapai County, Arizona", "125", "426", "19468", "04", "025"],
            ]).encode("utf-8")
        return fake_matrix(endpoint, params, key)

    return CensusMarketClient(
        transport=transport,
        key_loader=lambda: FAKE_KEY,
        max_calls=30,
    )


def _owner(platform, slug):
    return onboard_business(
        platform, business="Synthetic Market " + slug,
        owner_email="census-" + slug + "@fictional.example",
    )[0]


def _load(owner, area="prescott-valley", **extra):
    return owner.post(
        "/app/market-intelligence/run",
        data={"csrf": csrf_of(owner), "confirm": "LOAD PUBLIC CENSUS DATA",
              "area": area, **extra},
    )


def test_anonymous_visitors_cannot_load_market_or_make_requests(platform, monkeypatch):
    calls = []
    monkeypatch.setattr(market, "_CENSUS", type("Spy", (), {
        "query": lambda _, q: calls.append(q) or None,
    })())
    visitor = client_for(platform)
    assert visitor.get("/app/market-intelligence").status_code == 303
    assert visitor.post(
        "/app/market-intelligence/run",
        data={"area": "prescott-valley", "confirm": "LOAD PUBLIC CENSUS DATA"},
    ).status_code == 303
    assert calls == []


def test_page_is_offline_until_owner_explicitly_loads(platform, monkeypatch):
    calls = []
    monkeypatch.setattr(market, "_CENSUS", type("Spy", (), {
        "query": lambda _, q: calls.append(q) or None,
    })())
    owner = _owner(platform, "initial")
    response = owner.get("/app/market-intelligence")
    assert response.status_code == 200
    assert "Know your market" in response.text
    assert "Market intelligence" in response.text
    assert "Read-only request" in response.text
    assert "LOAD PUBLIC CENSUS DATA" in response.text
    assert 'name="csrf"' in response.text
    assert "/static/census_market.css" in response.text
    assert "Not yet measured" not in response.text
    assert "Latest available official statistics" not in response.text
    assert calls == []


def test_owner_loads_realistically_shaped_dated_public_aggregates(platform, monkeypatch):
    monkeypatch.setattr(market, "_CENSUS", _fake_service())
    owner = _owner(platform, "snapshot")
    response = _load(owner)
    assert response.status_code == 200, response.text[:1000]
    page = response.text
    assert "Prescott Valley market snapshot" in page
    for expected in (
        "49,179", "20,968", "971", "2,618", "148", "125",
        "Yavapai County", "Business ZIP 86314", "ACS ZCTA 86314",
        "2023", "2024", "thousand usd", "Source",
    ):
        assert expected in page
    assert FAKE_KEY not in page and "key=" not in page.lower()
    assert "not identical" in page
    assert "historical" in page.lower() or "historic" in page.lower()
    assert "not necessarily distinct companies" in page
    assert "No identified businesses" in page


def test_csrf_and_confirm_are_required_before_any_census_request(platform, monkeypatch):
    calls = []
    fake = _fake_service()
    def spy(query):
        calls.append(query)
        return fake.query(query)
    monkeypatch.setattr(market, "_CENSUS", type("Spy", (), {"query": lambda _, q: spy(q)})())
    owner = _owner(platform, "csrf")
    assert owner.post("/app/market-intelligence/run", data={
        "area": "prescott-valley", "confirm": "LOAD PUBLIC CENSUS DATA",
    }).status_code == 403
    assert owner.post("/app/market-intelligence/run", data={
        "area": "prescott-valley", "csrf": csrf_of(owner), "confirm": "NOT APPROVED",
    }).status_code == 422
    assert calls == []


def test_invalid_geography_never_reaches_provider(platform, monkeypatch):
    calls = []
    monkeypatch.setattr(market, "_CENSUS", type("Spy", (), {
        "query": lambda _, q: calls.append(q) or None,
    })())
    owner = _owner(platform, "invalid")
    for attack in ("", "prescott-valley/../../admin", "86314",
                   "prescott-valley?key=SHOULD_NOT_APPEAR",
                   "localhost:8000", "a" * 900):
        response = _load(owner, attack)
        assert response.status_code == 422, response.text[:300]
    assert calls == []


def test_registered_area_choices_have_distinct_place_and_zip_geographies():
    assert market.AREAS["prescott-valley"].place_fips == "57450"
    assert market.AREAS["prescott"].place_fips == "57380"
    assert market.AREAS["chino-valley"].place_fips == "12840"
    assert [q.product for _, q in market._read_requests(
        market.AREAS["prescott-valley"]
    )] == [
        "acs_place", "acs_zcta", "cbp_county", "cbp_county",
        "cbp_zip", "nonemp_county",
    ]
    assert all(isinstance(q, CensusQuery) for _, q in market._read_requests(
        market.AREAS["prescott-valley"]
    ))


def test_partial_census_failures_show_missing_not_zero(platform, monkeypatch):
    monkeypatch.setattr(market, "_CENSUS", _fake_service(
        fail_products={"nonemp_county", "county_remodelers"},
    ))
    owner = _owner(platform, "partial")
    response = _load(owner)
    assert response.status_code == 200
    assert "49,179" in response.text
    assert "Not reported" in response.text or "Not available" in response.text
    assert "county_nonemployers" in response.text
    assert "Missing data is not counted as zero." in response.text


def test_all_provider_failures_report_outage_and_no_invented_statistics(platform, monkeypatch):
    def fail(_query):
        raise CensusUnavailable("Upstream service unavailable")
    monkeypatch.setattr(market, "_CENSUS", type("Fake", (), {
        "query": lambda _, q: fail(q),
    })())
    owner = _owner(platform, "error")
    response = _load(owner)
    assert response.status_code == 200
    assert "Market data isn" in response.text
    assert "No business information was modified" in response.text
    assert "Latest available official statistics" not in response.text


def test_server_side_rate_limit_stops_provider_requests(platform, monkeypatch):
    calls = []
    monkeypatch.setattr(market, "rate_limited", lambda db, key, **kw: True)
    monkeypatch.setattr(market, "_CENSUS", type("Spy", (), {
        "query": lambda _, q: calls.append(q) or None,
    })())
    owner = _owner(platform, "limit")
    response = _load(owner)
    assert response.status_code == 200
    assert "Hourly Census refresh limit reached" in response.text
    assert calls == []


def test_other_business_host_cannot_access_workspace_market(platform):
    from .conftest import create_and_publish_site
    owner = _owner(platform, "host")
    host, _version = create_and_publish_site(
        owner, name="Synthetic Market Services", slug="market-local-synthetic"
    )
    website = client_for(platform, host)
    assert website.get("/app/market-intelligence").status_code == 404


def test_no_public_api_route_or_owner_key_reflected_in_html(platform, monkeypatch):
    monkeypatch.setattr(market, "_CENSUS", _fake_service())
    one = _owner(platform, "tenant-one")
    two = _owner(platform, "tenant-two")
    page_one = _load(one).text
    page_two = _load(two).text
    assert "Synthetic Market tenant-two" not in page_one
    assert "Synthetic Market tenant-one" not in page_two
    assert "Census API Key" not in page_one
    assert FAKE_KEY not in page_one
    assert FAKE_KEY not in page_two
