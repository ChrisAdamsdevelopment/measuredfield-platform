"""MF-052 synthetic DataForSEO geo vendor contract and provenance-adversarial tests."""
from __future__ import annotations

from pathlib import Path
import inspect

import pytest

from app.schemas.local_discovery import (
    CoordinateAnchor, GeoMarket, MatrixInputs, ProviderProfile, SearchPhrase,
    SearchSource,
)
from app.services.dataforseo_geo import (
    GeoVendorError, TrackedBusiness, inspect_dataforseo_response,
    prepare_dataforseo_request,
)
from app.services.search_matrix import build_search_matrix


@pytest.fixture
def matrix():
    return build_search_matrix(MatrixInputs(
        markets=(
            GeoMarket(id="pv", city="Prescott Valley", region="Arizona", country="USA"),
            GeoMarket(id="prescott", city="Prescott", region="Arizona", country="USA"),
        ),
        anchors=(
            CoordinateAnchor(id="pv-center", market_id="pv", label="PV downtown",
                             latitude=34.61, longitude=-112.32, radius_m=5000),
            CoordinateAnchor(id="p-center", market_id="prescott", label="Prescott downtown",
                             latitude=34.54, longitude=-112.47, radius_m=5000),
        ),
        phrases=(SearchPhrase(id="kitchen", base_text="kitchen remodeling",
                              variants=("kitchen remodeling near me",)),),
        providers=(
            ProviderProfile(id="dataforseo-organic", source=SearchSource.ORGANIC_SERP,
                            localization="coordinate_radius",
                            supports_device=True, supports_locale=True),
            ProviderProfile(id="dataforseo-pack", source=SearchSource.LOCAL_PACK_SERP,
                            localization="coordinate_radius",
                            supports_device=True, supports_locale=True),
        ),
        requested_device="mobile", requested_locale="en-US",
        max_cells=30, max_unique_requests=30,
    ))


@pytest.fixture
def organic(matrix):
    cell = next(c for c in matrix.cells if c.source == SearchSource.ORGANIC_SERP
                and c.market_id == "pv")
    return prepare_dataforseo_request(cell, surface="organic")


@pytest.fixture
def pack(matrix):
    cell = next(c for c in matrix.cells if c.source == SearchSource.LOCAL_PACK_SERP
                and c.market_id == "pv")
    return prepare_dataforseo_request(cell, surface="local_pack")


def organic_item(rank=1, domain="example-builders.com", *, paid=False):
    return {
        "type": "organic", "title": "Example Builders — Kitchen Remodels",
        "rank_group": rank, "rank_absolute": rank, "domain": domain,
        "is_paid": paid,
    }


def local_item(rank=1, *, kind="local_pack", cid="71234567890",
               place_id="ChIJExampleA", domain="example-builders.com", paid=False):
    return {
        "type": kind, "title": "Example Builders", "rank_group": rank,
        "rank_absolute": rank, "cid": cid, "place_id": place_id,
        "domain": domain, "is_paid": paid,
    }


def vendor_fixture(request, *, items=None):
    if items is None:
        if request.surface == "organic":
            items = [organic_item(), organic_item(2, "rival-remodeler.net")]
        else:
            kind = "maps_search" if request.surface == "google_maps" else "local_pack"
            items = [local_item(kind=kind),
                     local_item(2, kind=kind, cid="99887766",
                                place_id="ChIJExampleB", domain="rival-remodeler.net")]
    expected_type = "organic" if request.surface in ("organic", "local_pack") else (
        "maps" if request.surface == "google_maps" else "local_finder"
    )
    se_type = request.endpoint.split("/")[-3]
    return {
        "status_code": 20000,
        "tasks_error": 0, "tasks_count": 1, "cost": 0.002,
        "tasks": [{
            "id": "12345678-1234-1234-0000-123456789012",
            "status_code": 20000, "result_count": 1, "cost": 0.002,
            "path": request.endpoint.split("/")[3:],
            "data": {
                "api": "serp", "function": "live", "se": "google",
                "se_type": se_type, **request.payload(),
            },
            "result": [{
                "keyword": request.payload()["keyword"], "type": expected_type,
                "language_code": "en",
                "datetime": "2026-11-16 12:46:16 +00:00",
                "items_count": len(items), "items": items,
            }],
        }],
    }


def expected_owner(surface):
    return (TrackedBusiness(domain="https://www.example-builders.com")
            if surface == "organic"
            else TrackedBusiness(cid="71234567890", place_id="ChIJExampleA"))


def test_request_planner_has_exact_localized_vendor_endpoint_for_all_surfaces(matrix):
    org = next(c for c in matrix.cells if c.provider_id == "dataforseo-organic"
               and c.market_id == "pv")
    pack = next(c for c in matrix.cells if c.provider_id == "dataforseo-pack"
                and c.market_id == "pv")
    for surface in ("organic", "local_pack", "google_maps", "local_finder"):
        req = prepare_dataforseo_request(
            org if surface == "organic" else pack, surface=surface, zoom=14)
        assert req.endpoint.startswith("https://api.dataforseo.com/v3/serp/google/")
        assert req.endpoint.endswith("/live/advanced")
        assert req.payload()["keyword"] == "kitchen remodeling near me"
        assert req.payload()["device"] == "mobile"
        assert req.payload()["language_code"] == "en"
        assert req.payload()["tag"] == "mf052-" + req.cell_id
        assert req.payload()["depth"] == 20
        assert req.execution_performed is False
        assert len(req.plan_signature) == 64 and len(req.request_signature) == 64
        assert req.latitude == 34.61 and req.longitude == -112.32
        if surface in ("organic", "local_pack"):
            assert req.payload()["location_coordinate"] == "34.61,-112.32,5000"
            assert req.requested_radius_m == 5000 and req.zoom is None
        else:
            assert req.payload()["location_coordinate"] == "34.61,-112.32,14z"
            assert req.zoom == 14 and req.requested_radius_m is None
    assert prepare_dataforseo_request(org, surface="organic") == (
        prepare_dataforseo_request(org, surface="organic"))
    assert (prepare_dataforseo_request(pack, surface="google_maps").request_signature
            != prepare_dataforseo_request(pack, surface="local_finder").request_signature)


def test_prescott_and_prescott_valley_are_separate_actual_coordinate_requests(matrix):
    cells = [c for c in matrix.cells if c.source == SearchSource.ORGANIC_SERP]
    requests = [prepare_dataforseo_request(c, surface="organic") for c in cells]
    assert len(requests) == 2
    assert len({r.request_signature for r in requests}) == 2
    assert {r.payload()["location_coordinate"] for r in requests} == {
        "34.61,-112.32,5000", "34.54,-112.47,5000",
    }


@pytest.mark.parametrize("surface", ["organic", "local_pack", "google_maps", "local_finder"])
def test_fixture_parser_keeps_four_surfaces_distinct(matrix, surface):
    cell = next(c for c in matrix.cells if c.source == (
        SearchSource.ORGANIC_SERP if surface == "organic" else SearchSource.LOCAL_PACK_SERP))
    request = prepare_dataforseo_request(cell, surface=surface)
    response = vendor_fixture(request)
    result = inspect_dataforseo_response(request, response, owner=expected_owner(surface))
    assert result["surface"] == surface
    assert result["owner_position"] == 1
    assert result["owner_position_status"] == "observed_in_returned_items"
    assert result["ranked_competitors"][0]["rank"] == 2
    assert result["requested_latitude"] == 34.61
    assert result["requested_longitude"] == -112.32
    assert result["cost_micro_usd_reported"] == 2000
    assert result["observed_at"] == "2026-11-16T12:46:16+00:00"
    assert result["request_signature"] == request.request_signature
    assert result["plan_signature"] == request.plan_signature
    assert len(result["response_sha256"]) == 64
    assert not result["independently_verified"]
    assert result["provider_calls_made"] == 0
    assert not result["charge_authorized_or_executed"]
    assert result["evidence_class"] == "owner_imported_unverified"


def test_verified_field_in_untrusted_json_cannot_elevate_provenance(organic):
    response = vendor_fixture(organic)
    response["verified"] = True
    response["receipt_authenticated"] = True
    response["tasks"][0]["result"][0]["independently_verified"] = True
    parsed = inspect_dataforseo_response(
        organic, response, owner=TrackedBusiness(domain="example-builders.com"))
    assert parsed["independently_verified"] is False
    assert parsed["evidence_class"] == "owner_imported_unverified"


def test_no_rank_is_unknown_not_101_or_absent(organic):
    source = vendor_fixture(organic, items=[organic_item(1, "rival-remodeler.net")])
    result = inspect_dataforseo_response(
        organic, source, owner=TrackedBusiness(domain="example-builders.com"))
    assert result["owner_position"] is None
    assert result["owner_position_status"] == "unknown_not_in_returned_items"


def test_sponsored_items_are_excluded_from_organic_rank(organic):
    source = vendor_fixture(organic, items=[
        organic_item(1, "example-builders.com", paid=True),
        organic_item(2, "rival-remodeler.net"),
    ])
    result = inspect_dataforseo_response(
        organic, source, owner=TrackedBusiness(domain="example-builders.com"))
    assert result["owner_position"] is None and result["returned_ranked_items"] == 1


def test_homograph_domain_and_display_name_do_not_count_as_owner(organic):
    fake = organic_item(1, "fake-example-builders.com")
    fake["title"] = "Example Builders"
    result = inspect_dataforseo_response(
        organic, vendor_fixture(organic, items=[fake]),
        owner=TrackedBusiness(domain="example-builders.com"))
    assert result["owner_position"] is None


def test_only_cid_or_place_id_match_mapped_business_not_name(pack):
    fake = local_item(1, cid="987654321", place_id="ChIJAttacker")
    fake["title"] = "Example Builders"
    result = inspect_dataforseo_response(
        pack, vendor_fixture(pack, items=[fake]),
        owner=TrackedBusiness(cid="71234567890"))
    assert result["owner_position"] is None
    with pytest.raises(GeoVendorError, match="name-only matching"):
        TrackedBusiness()


@pytest.mark.parametrize("mutation,part", [
    (lambda r: r.update(status_code=40000), "did not complete"),
    (lambda r: r.update(tasks_error=1), "did not complete"),
    (lambda r: r.update(tasks_count=2), "did not complete"),
    (lambda r: r["tasks"][0].update(status_code=40000), "did not complete"),
    (lambda r: r["tasks"][0].update(result_count=0), "did not complete"),
    (lambda r: r["tasks"][0].update(data=None), "parameters are missing"),
    (lambda r: r["tasks"][0]["data"].update(keyword="other keyword"), "echo mismatch: keyword"),
    (lambda r: r["tasks"][0]["data"].update(location_coordinate="34.54,-112.47,5000"),
     "echo mismatch: location_coordinate"),
    (lambda r: r["tasks"][0]["data"].update(device="desktop"), "echo mismatch: device"),
    (lambda r: r["tasks"][0]["data"].update(tag="attacker"), "echo mismatch: tag"),
    (lambda r: r["tasks"][0]["data"].update(se_type="maps"), "search engine family mismatch"),
    (lambda r: r["tasks"][0].update(path=["v3", "serp", "google", "maps", "live", "advanced"]),
     "path does not match"),
    (lambda r: r["tasks"][0]["result"][0].update(type="maps"), "metadata mismatch"),
    (lambda r: r["tasks"][0]["result"][0].update(datetime="2026-11-16T12:46:16"), "timezone"),
    (lambda r: r["tasks"][0]["result"][0].update(datetime="nope"), "missing or malformed"),
    (lambda r: r.update(cost=0.004), "billed totals disagree"),
    (lambda r: r["tasks"][0].update(cost="NaN"), "supported reporting range"),
    (lambda r: r["tasks"][0].update(cost=0.0000001), "sub-microUSD"),
    (lambda r: r["tasks"][0]["result"][0].update(items_count=100), "items_count"),
])
def test_response_fails_closed_on_provider_status_request_mismatch_and_cost(
        organic, mutation, part):
    response = vendor_fixture(organic)
    mutation(response)
    with pytest.raises(GeoVendorError, match=part):
        inspect_dataforseo_response(
            organic, response, owner=TrackedBusiness(domain="example-builders.com"))


def test_rank_collisions_cannot_claim_a_stable_position(organic):
    response = vendor_fixture(organic, items=[
        organic_item(1), organic_item(1, "rival-remodeler.net")])
    with pytest.raises(GeoVendorError, match="Duplicate rank_group"):
        inspect_dataforseo_response(
            organic, response, owner=TrackedBusiness(domain="example-builders.com"))
    response["tasks"][0]["result"][0]["items"][1]["rank_group"] = True
    with pytest.raises(GeoVendorError, match="Invalid group rank"):
        inspect_dataforseo_response(
            organic, response, owner=TrackedBusiness(domain="example-builders.com"))


def test_conflicting_local_cid_positions_rejected(pack):
    source = vendor_fixture(pack, items=[
        local_item(1),
        local_item(2, cid="71234567890", place_id="ChIJExampleA"),
    ])
    with pytest.raises(GeoVendorError, match="conflicting positions"):
        inspect_dataforseo_response(pack, source, owner=TrackedBusiness(cid="71234567890"))


@pytest.mark.parametrize("bad_identity", [
    {"domain": "https://admin:password@example-builders.com"},
    {"domain": "localhost"},
    {"cid": "one-two-three"},
    {"cid": ""},
    {"place_id": "x\nspoof"},
])
def test_owner_identity_validation(bad_identity):
    with pytest.raises(GeoVendorError):
        TrackedBusiness(**bad_identity)


def test_request_does_not_mutate_mf021_cell_and_signature_tampering_rejected(matrix):
    cell = next(c for c in matrix.cells if c.source == SearchSource.ORGANIC_SERP)
    first = prepare_dataforseo_request(cell, surface="organic")
    assert first.cell_id == cell.id
    with pytest.raises(GeoVendorError, match="request signature"):
        prepare_dataforseo_request(
            cell.model_copy(update={"request_signature": "0" * 64}), surface="organic")
    with pytest.raises(GeoVendorError, match="source does not match"):
        prepare_dataforseo_request(cell, surface="google_maps")
    with pytest.raises(GeoVendorError, match="coordinate-capable"):
        prepare_dataforseo_request(
            cell.model_copy(update={"coordinate_parameter_applied": False}), surface="organic")


def test_request_rejects_invalid_unsupported_vendor_parameters(matrix):
    cell = next(c for c in matrix.cells if c.source == SearchSource.ORGANIC_SERP)
    with pytest.raises(GeoVendorError, match="zoom"):
        prepare_dataforseo_request(cell, surface="organic", zoom=22)
    with pytest.raises(GeoVendorError, match="depth"):
        prepare_dataforseo_request(cell, surface="organic", depth=0)
    with pytest.raises(GeoVendorError, match="Unsupported search surface"):
        prepare_dataforseo_request(cell, surface="places")  # type: ignore[arg-type]


def test_ranked_parser_is_network_free_and_secret_independent():
    source = inspect.getsource(prepare_dataforseo_request)
    source += inspect.getsource(inspect_dataforseo_response)
    assert "httpx" not in source and "requests.post" not in source
    assert "Authorization" not in source and "getenv" not in source
    repo = Path(__file__).resolve().parents[1]
    assert (repo / "docs" / "MF052_DATAFORSEO_GEO_PROVIDER.md").exists()
