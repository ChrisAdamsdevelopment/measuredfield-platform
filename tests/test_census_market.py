"""MF-073 public Census adapter tests; all provider data is fabricated in memory."""
from __future__ import annotations

from dataclasses import asdict
import json

import pytest

from app.services import census_market as cm

FAKE_KEY = "A" * 40
COUNTY = cm.CensusQuery("cbp_county", state="04", county="025")
ZIP = cm.CensusQuery("cbp_zip", zip_code="86314")
NONEMP = cm.CensusQuery("nonemp_county", state="04", county="025")
PLACE = cm.CensusQuery("acs_place", state="04", place="57450")
PLACES = cm.CensusQuery("acs_places", state="04")
ACS_COUNTY = cm.CensusQuery("acs_county", state="04", county="025")
ACS_ZCTA = cm.CensusQuery("acs_zcta", zip_code="86314")


def fake_matrix(endpoint, params, key):
    assert key == FAKE_KEY
    assert endpoint in ("/data/2023/cbp", "/data/2024/nonemp", "/data/2024/acs/acs5")
    if endpoint == "/data/2023/cbp":
        if "county:" in params["for"]:
            headers = ["NAME", "ESTAB", "EMP", "PAYANN", "state", "county"]
            rows = [["Yavapai County, Arizona", "971", "6829", "387208", "04", "025"]]
        else:
            headers = ["NAME", "ESTAB", "zip code"]
            rows = [["ZIP 86314", "148", "86314"]]
    elif endpoint == "/data/2024/nonemp":
        headers = ["NAME", "NESTAB", "NRCPTOT", "state", "county"]
        rows = [["Yavapai County, Arizona", "2618", "228091", "04", "025"]]
    elif params["for"].startswith("county"):
        headers = ["NAME", "B01003_001E", "B11001_001E", "state", "county"]
        rows = [["Yavapai County, Arizona", "240000", "105000", "04", "025"]]
    elif params["for"].startswith("zip code tabulation area"):
        headers = ["NAME", "B01003_001E", "B11001_001E", "zip code tabulation area"]
        rows = [["ZCTA5 86314", "25000", "10000", "86314"]]
    else:
        headers = ["NAME", "B01003_001E", "B11001_001E", "state", "place"]
        rows = [["Prescott Valley town, Arizona", "49179", "20968", "04", "57450"]]
    return json.dumps([headers, *rows]).encode("utf-8")


def client(*, transport=fake_matrix, max_calls=10, ttl_seconds=3600, tick=lambda: 100.0):
    return cm.CensusMarketClient(
        transport=transport, key_loader=lambda: FAKE_KEY,
        max_calls=max_calls, ttl_seconds=ttl_seconds, tick=tick,
    )


@pytest.mark.parametrize(("query", "metric_key", "value", "unit", "year", "geo"), [
    (COUNTY, "ESTAB", 971, "establishments", 2023, "county_fips"),
    (COUNTY, "PAYANN", 387208, "thousand_usd", 2023, "county_fips"),
    (ZIP, "ESTAB", 148, "establishments", 2023, "business_zip"),
    (NONEMP, "NESTAB", 2618, "establishments", 2024, "county_fips"),
    (NONEMP, "NRCPTOT", 228091, "thousand_usd", 2024, "county_fips"),
    (PLACE, "B01003_001E", 49179, "persons", 2024, "census_place"),
    (PLACE, "B11001_001E", 20968, "households", 2024, "census_place"),
    (ACS_COUNTY, "B01003_001E", 240000, "persons", 2024, "county_fips"),
    (ACS_ZCTA, "B11001_001E", 10000, "households", 2024, "acs_zcta_not_postal_zip"),
])
def test_dataset_geography_and_unit_labels(query, metric_key, value, unit, year, geo):
    record = client().query(query).observations[0]
    matching = [m for m in record.metrics if m.field == metric_key]
    assert len(matching) == 1
    assert matching[0].value == value
    assert matching[0].unit == unit
    assert matching[0].availability == "observed"
    assert record.reference_year == year
    assert record.geography_kind == geo
    assert record.name
    assert record.source_url.startswith("https://api.census.gov/data/")
    assert "key=" not in record.source_url
    assert "api.census.gov" in record.source_url
    assert record.naics_edition == ("2017" if year == 2023 else
                                    ("2022" if query == NONEMP else None))
    assert "key=" not in json.dumps(asdict(record)).lower()


def test_acs_listing_is_a_collection_not_guessed_city_boundary():
    outcome = client().query(PLACES)
    assert outcome.product == "acs_places"
    assert len(outcome.observations) == 1
    assert outcome.observations[0].name.startswith("Prescott Valley")
    assert "place:*" not in outcome.source_url  # percent-encoded
    assert "place%3A%2A" in outcome.source_url


@pytest.mark.parametrize("kwargs", [
    {"product": "cbp_county", "state": "04", "county": "25"},
    {"product": "cbp_county", "state": "04", "county": "025", "zip_code": "86314"},
    {"product": "cbp_zip", "zip_code": "86314&key=STEAL"},
    {"product": "acs_place", "state": "04", "place": "Prescott Valley"},
    {"product": "acs_places", "state": "04", "place": "57450"},
    {"product": "nonemp_county", "state": "04", "county": "025", "naics": "23&evil=1"},
    {"product": "acs_zcta", "zip_code": "86314", "state": "04"},
    {"product": "acs_county", "state": "04", "county": "025", "naics": "999999"},
    {"product": "malicious", "state": "04"},
])
def test_unapproved_geography_or_query_injection_rejected(kwargs):
    with pytest.raises(ValueError):
        cm.CensusQuery(**kwargs)


def test_key_file_parsed_at_runtime_without_value_in_results(tmp_path):
    secret_path = tmp_path / "census.env"
    secret_path.write_text("CENSUS_API_KEY=" + FAKE_KEY + "\n", encoding="utf-8")
    assert cm.read_census_api_key(environ={}, local_key_file=secret_path) == FAKE_KEY
    result = cm.CensusMarketClient(
        key_loader=lambda: cm.read_census_api_key(
            environ={}, local_key_file=secret_path
        ),
        transport=fake_matrix,
    ).query(COUNTY)
    assert FAKE_KEY not in repr(result)
    assert FAKE_KEY not in json.dumps(asdict(result))


@pytest.mark.parametrize("broken", [
    "CENSUS_API_KEY=" + "S" * 39,
    "WRONG_KEY=" + FAKE_KEY,
    "CENSUS_API_KEY=" + FAKE_KEY + "\nCENSUS_API_KEY=" + FAKE_KEY,
    "plaintext-key-is-not-an-env-file",
])
def test_invalid_env_file_never_echoes_secret(tmp_path, broken):
    file = tmp_path / "broken.env"
    file.write_text(broken, encoding="utf-8")
    with pytest.raises(cm.CensusConfigurationError) as exc:
        cm.read_census_api_key(environ={}, local_key_file=file)
    assert FAKE_KEY not in str(exc.value)


def test_safe_missing_secret_and_transport_failure_does_not_disclose_url(tmp_path):
    with pytest.raises(cm.CensusConfigurationError):
        cm.read_census_api_key(environ={}, local_key_file=tmp_path / "absent.env")
    def leak(url, fields, key):
        raise RuntimeError("private Census key " + key)
    with pytest.raises(cm.CensusUnavailable) as exc:
        client(transport=leak).query(COUNTY)
    assert FAKE_KEY not in str(exc.value)
    assert "private Census key" not in str(exc.value)


def test_cache_reuses_same_public_aggregate_without_reusing_key_in_records():
    calls = []
    ticks = [100.0]

    def transport(endpoint, fields, key):
        calls.append(endpoint)
        return fake_matrix(endpoint, fields, key)

    c = client(transport=transport, max_calls=1, tick=lambda: ticks[0], ttl_seconds=30)
    first = c.query(COUNTY)
    second = c.query(COUNTY)
    assert first.cache_hit is False and second.cache_hit is True
    assert len(calls) == 1
    ticks[0] = 200
    with pytest.raises(cm.CensusAccessError, match="budget"):
        c.query(COUNTY)


def test_suppressed_or_negative_census_cells_do_not_become_zero_or_negative():
    def suppressed(endpoint, params, key):
        return json.dumps([
            ["NAME", "ESTAB", "EMP", "PAYANN", "state", "county"],
            ["Yavapai County, Arizona", "S", "-666666666", "0", "04", "025"],
        ]).encode("utf-8")
    record = client(transport=suppressed).query(COUNTY).observations[0]
    values = {m.field: m for m in record.metrics}
    assert values["ESTAB"].value is None
    assert values["EMP"].value is None
    assert values["ESTAB"].availability == "suppressed_or_not_available"
    assert values["PAYANN"].value == 0  # explicit observed zero, not missing


@pytest.mark.parametrize("bad_response", [
    b"invalid-json",
    b"[]",
    json.dumps([["NAME", "ESTAB"], ["Yavapai County"]]).encode("utf-8"),
    json.dumps([
        ["NAME", "ESTAB", "EMP", "PAYANN", "state", "county"],
        ["Wrong county", "971", "6829", "387208", "04", "013"],
    ]).encode("utf-8"),
    json.dumps([
        ["NAME", "ESTAB", "EMP", "PAYANN", "state", "county"],
        ["Yavapai County", "971", "6829", "387208", "04"],
    ]).encode("utf-8"),
])
def test_bad_or_wrong_geography_provider_responses_fail_closed(bad_response):
    with pytest.raises(cm.CensusUnavailable):
        client(transport=lambda *_: bad_response).query(COUNTY)


def test_request_budget_and_url_allowlist_and_size_limits():
    with pytest.raises(cm.CensusAccessError):
        cm._safe_get("/api/evil", {}, FAKE_KEY)
    with pytest.raises(cm.CensusUnavailable):
        client(transport=lambda *_: b"0" * (cm.MAX_RESPONSE_BYTES + 1)).query(COUNTY)
    with pytest.raises(ValueError):
        cm.CensusMarketClient(max_calls=999999)
    with pytest.raises(ValueError):
        cm.CensusMarketClient(ttl_seconds=0)


def test_rejects_response_with_more_than_maximum_rows():
    extra = [
        ["NAME", "B01003_001E", "B11001_001E", "state", "place"]
    ] + [["Synthetic", "1", "1", "04", "57450"]] * (cm.MAX_RESPONSE_ROWS + 1)
    with pytest.raises(cm.CensusUnavailable):
        client(transport=lambda *_: json.dumps(extra).encode()).query(PLACES)


def test_labelled_coverage_explicitly_excludes_named_businesses_and_mismatched_years():
    employer = client().query(COUNTY).observations[0]
    nonemployer = client().query(NONEMP).observations[0]
    assert employer.naics_edition != nonemployer.naics_edition
    assert employer.reference_year != nonemployer.reference_year
    assert "not named firms" in employer.caveats[0]
    assert "no paid employees" in nonemployer.caveats[0]
    assert not hasattr(employer, "business_names")
