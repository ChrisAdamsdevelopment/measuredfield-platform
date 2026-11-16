"""Deterministic, synthetic location-search and competitor identity contract tests."""
from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json

import pytest
from pydantic import ValidationError

from app.schemas.local_discovery import (
    AliasReview, BusinessCandidate, CoordinateAnchor, EvidenceClass,
    GeoMarket, MatrixInputs, Presence, ProviderProfile, SearchPhrase,
    SearchSource, SourceObservation,
)
from app.services.search_matrix import (
    IdentityConflict, MatrixLimitError, build_search_matrix, canonical_json,
    metric_description, normalize_domain, resolve_competitor_identities,
    validate_evidence_for_cell,
)


@pytest.fixture
def inputs():
    return MatrixInputs(
        markets=(
            GeoMarket(id="prescott", city="Prescott", region="Arizona", country="USA"),
            GeoMarket(id="phoenix", city="Phoenix", region="Arizona", country="USA"),
        ),
        anchors=(
            CoordinateAnchor(id="p1", market_id="prescott", label="Downtown",
                             latitude=34.54, longitude=-112.47, radius_m=5000),
            CoordinateAnchor(id="p2", market_id="prescott", label="North",
                             latitude=34.60, longitude=-112.40, radius_m=5000),
            CoordinateAnchor(id="h1", market_id="phoenix", label="Metro",
                             latitude=33.448, longitude=-112.073, radius_m=8000),
        ),
        phrases=(
            SearchPhrase(id="remodel", base_text="kitchen remodeling",
                         variants=("kitchen contractor", "kitchen remodeler")),
            SearchPhrase(id="deck", base_text="deck building",
                         variants=("deck builder",)),
        ),
        providers=(
            ProviderProfile(id="places-fixture", source=SearchSource.PLACES,
                            localization="coordinate_radius"),
            ProviderProfile(id="serp-fixture", source=SearchSource.ORGANIC_SERP,
                            localization="market_name", supports_device=True),
        ),
        requested_device="mobile",
        requested_locale="en-US",
        max_cells=100,
        max_unique_requests=100,
    )


def test_complete_matrix_and_actual_request_dedup(inputs):
    plan = build_search_matrix(inputs)
    assert plan.logical_cells == len(plan.cells) == 18
    assert plan.distinct_provider_requests == 15
    assert plan.unsupported_coordinate_cells == 9
    assert plan.unsupported_device_cells == 9
    assert plan.unsupported_locale_cells == 18
    assert plan.execution_performed is False
    assert {cell.status for cell in plan.cells} == {"planned_not_executed"}


def test_matrix_is_reproducible_without_network_or_mutation(inputs):
    first = build_search_matrix(inputs)
    second = build_search_matrix(inputs)
    assert first == second
    assert len({cell.id for cell in first.cells}) == len(first.cells)
    assert all(len(cell.request_signature) == 64 for cell in first.cells)
    with pytest.raises((ValidationError, TypeError)):
        first.logical_cells = 100


def test_coordinate_request_applies_only_to_supported_provider(inputs):
    cells = build_search_matrix(inputs).cells
    places = [c for c in cells if c.source == SearchSource.PLACES]
    serp = [c for c in cells if c.source == SearchSource.ORGANIC_SERP]
    assert len(places) == len(serp) == 9
    for cell in places:
        args = json.loads(cell.request_json)
        assert args["radius_m"] in {5000, 8000}
        assert -180 <= args["longitude"] <= 180
        assert "market" not in args
        assert "device" not in args
        assert cell.coordinate_parameter_applied
        assert not cell.device_parameter_applied
    for cell in serp:
        args = json.loads(cell.request_json)
        assert args["market"]["country"] == "USA"
        assert args["device"] == "mobile"
        assert "latitude" not in args and "longitude" not in args
        assert not cell.coordinate_parameter_applied
        assert cell.device_parameter_applied


def test_unsupported_anchors_do_not_invent_distinct_localized_serps(inputs):
    serp = [c for c in build_search_matrix(inputs).cells
            if c.source == SearchSource.ORGANIC_SERP and c.market_id == "prescott"]
    assert len(serp) == 6
    assert len({c.request_signature for c in serp}) == 3


def test_exact_cell_cap_not_silent_truncation(inputs):
    with pytest.raises(MatrixLimitError, match="18 logical cells"):
        build_search_matrix(inputs.model_copy(update={"max_cells": 17}))


def test_unique_request_cap_not_silent_truncation(inputs):
    with pytest.raises(MatrixLimitError, match="15 distinct provider requests"):
        build_search_matrix(inputs.model_copy(update={"max_unique_requests": 14}))


@pytest.mark.parametrize("lat,lon", [
    (91, -112), (-91, -112), (34, 181), (34, -181),
    (float("nan"), -112), (34, float("inf")),
])
def test_invalid_or_nonfinite_coordinates_rejected(lat, lon):
    with pytest.raises(ValidationError):
        CoordinateAnchor(id="x", market_id="prescott", label="Test",
                         latitude=lat, longitude=lon, radius_m=5000)


@pytest.mark.parametrize("radius", [0, 49, 50001])
def test_invalid_search_radius_rejected(radius):
    with pytest.raises(ValidationError):
        CoordinateAnchor(id="x", market_id="prescott", label="Test",
                         latitude=34, longitude=-112, radius_m=radius)


def test_anchor_must_belong_to_selected_market(inputs):
    bad = CoordinateAnchor(id="orphan", market_id="unknown", label="Wrong",
                           latitude=34, longitude=-112, radius_m=1000)
    with pytest.raises(ValidationError, match="anchor must belong"):
        MatrixInputs.model_validate({**inputs.model_dump(),
                                     "anchors": [bad.model_dump()]})


def test_every_market_requires_anchor(inputs):
    with pytest.raises(ValidationError, match="requires at least one"):
        MatrixInputs.model_validate({**inputs.model_dump(),
                                     "anchors": [inputs.anchors[0].model_dump()]})


def test_duplicate_anchor_ids_rejected(inputs):
    with pytest.raises(ValidationError, match="Duplicate anchor IDs"):
        MatrixInputs.model_validate({**inputs.model_dump(),
                                     "anchors": [inputs.anchors[0].model_dump()] * 2 +
                                     [inputs.anchors[2].model_dump()]})


@pytest.mark.parametrize("variants", [
    ("Kitchen Remodel", "kitchen remodel"),
    ("",),
    ("bad\nline",),
])
def test_invalid_query_variants_rejected(variants):
    with pytest.raises(ValidationError):
        SearchPhrase(id="x", base_text="Kitchen", variants=variants)


def test_search_console_is_not_coordinate_rank_provider():
    with pytest.raises(ValidationError, match="Search Console"):
        ProviderProfile(id="gsc", source=SearchSource.SEARCH_CONSOLE,
                        localization="coordinate_radius")
    provider = ProviderProfile(id="gsc", source=SearchSource.SEARCH_CONSOLE,
                               localization="none")
    assert provider.localization == "none"


def test_source_labels_do_not_call_places_presence_a_serp_rank():
    assert "not organic or map-pack rank" in metric_description(SearchSource.PLACES)
    assert "not competitor rank" in metric_description(SearchSource.SEARCH_CONSOLE)
    assert "provider receipt" in metric_description(SearchSource.ORGANIC_SERP)
    assert "provider receipt" in metric_description(SearchSource.LOCAL_PACK_SERP)


def _obs(cell, **overrides):
    payload = dict(
        cell_id=cell.id, request_signature=cell.request_signature,
        provider_id=cell.provider_id, source=cell.source,
        observed_at=datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc),
        provider_receipt_id="synthetic-provider-receipt-001",
        evidence_class=EvidenceClass.VERIFIED_PROVIDER,
        presence=Presence.OBSERVED,
    )
    payload.update(overrides)
    return SourceObservation(**payload)


def test_correct_provider_evidence_matches_exact_immutable_query(inputs):
    cell = next(c for c in build_search_matrix(inputs).cells
                if c.source == SearchSource.ORGANIC_SERP)
    observation = _obs(cell, rank_position=3)
    assert validate_evidence_for_cell(cell, observation) is None


@pytest.mark.parametrize("override", [
    {"request_signature": "0" * 64},
    {"cell_id": "1" * 20},
    {"provider_id": "different-provider"},
    {"source": SearchSource.LOCAL_PACK_SERP},
])
def test_mismatched_receipt_cannot_attach_to_cell(inputs, override):
    cell = next(c for c in build_search_matrix(inputs).cells
                if c.source == SearchSource.ORGANIC_SERP)
    obs = _obs(cell, **override)
    with pytest.raises(ValueError, match="match"):
        validate_evidence_for_cell(cell, obs)


def test_places_presence_is_not_a_rank(inputs):
    places = next(c for c in build_search_matrix(inputs).cells
                  if c.source == SearchSource.PLACES)
    assert _obs(places).rank_position is None
    with pytest.raises(ValidationError, match="SERP source"):
        _obs(places, rank_position=1)


def test_only_verified_provider_serps_may_have_rank(inputs):
    cell = next(c for c in build_search_matrix(inputs).cells
                if c.source == SearchSource.ORGANIC_SERP)
    for evidence_class in (EvidenceClass.SYNTHETIC, EvidenceClass.OWNER_IMPORT):
        with pytest.raises(ValidationError, match="not verified"):
            _obs(cell, rank_position=1, evidence_class=evidence_class)
    with pytest.raises(ValidationError, match="cannot have"):
        _obs(cell, rank_position=2, presence=Presence.UNKNOWN)
    assert _obs(cell, rank_position=1).rank_position == 1


def test_utc_offset_required_for_observations(inputs):
    cell = build_search_matrix(inputs).cells[0]
    with pytest.raises(ValidationError, match="timezone"):
        _obs(cell, observed_at=datetime(2026, 11, 15, 20, 0))


def test_owner_import_cannot_establish_not_observed(inputs):
    cell = build_search_matrix(inputs).cells[0]
    with pytest.raises(ValidationError, match="absence"):
        _obs(cell, presence=Presence.NOT_OBSERVED,
             evidence_class=EvidenceClass.OWNER_IMPORT)


def test_gsc_average_position_is_separate_from_organic_result_position(inputs):
    organic = next(c for c in build_search_matrix(inputs).cells
                   if c.source == SearchSource.ORGANIC_SERP)
    with pytest.raises(ValidationError, match="GSC average"):
        _obs(organic, gsc_average_position=4.1)
    gsc = SourceObservation(
        cell_id="a" * 20, request_signature="b" * 64,
        provider_id="gsc", source=SearchSource.SEARCH_CONSOLE,
        observed_at=datetime.now(timezone.utc), provider_receipt_id="source-1",
        evidence_class=EvidenceClass.OWNER_IMPORT,
        presence=Presence.OBSERVED, gsc_average_position=4.1,
    )
    assert gsc.rank_position is None


def test_canonical_json_is_repeatable_and_sorted():
    assert canonical_json({"z": 3, "a": 1}) == '{"a":1,"z":3}'


def _candidate(id, name="Canyon Kitchens", city="Prescott", provider="places",
               place=None, domain=None, verified=False):
    return BusinessCandidate(id=id, provider_id=provider, name=name, city=city,
                             provider_place_id=place, website_domain=domain,
                             domain_verified=verified, receipt_id="fixture-" + id)


def test_place_id_same_provider_automatically_merges():
    a = _candidate("a", place="place-777")
    b = _candidate("b", name="Canyon Remodeling", place="place-777")
    result = resolve_competitor_identities([a, b])
    assert result.clusters == (("a", "b"),)
    assert result.automatic_merges == 1


def test_unverified_same_name_and_city_only_requests_review():
    result = resolve_competitor_identities([_candidate("a"), _candidate("b")])
    assert result.clusters == (("a",), ("b",))
    assert result.manual_review_pairs == (("a", "b", "name_and_city_only"),)


def test_verified_domain_and_identity_name_city_without_place_ids_auto_merge():
    result = resolve_competitor_identities([
        _candidate("a", domain="https://www.canyon.example/", verified=True),
        _candidate("b", domain="canyon.example", verified=True),
    ])
    assert result.clusters == (("a", "b"),)


def test_one_brand_multiple_known_location_place_ids_stays_distinct():
    result = resolve_competitor_identities([
        _candidate("a", place="place-1", domain="canyon.example", verified=True),
        _candidate("b", place="place-2", domain="canyon.example", verified=True),
    ])
    assert result.clusters == (("a",), ("b",))
    assert result.manual_review_pairs == ()
    assert result.blocked_conflicts == (("a", "b", "distinct_provider_place_ids"),)


def test_reviewer_approval_requires_real_provenance_and_no_provider_conflict():
    a, b = _candidate("a"), _candidate("b")
    result = resolve_competitor_identities(
        [a, b], [AliasReview(left_id="a", right_id="b", approved=True,
                             reviewed_by="local owner", evidence_ref="receipt-1")]
    )
    assert result.clusters == (("a", "b"),)
    assert result.owner_reviewed_merges == 1


def test_conflicting_same_provider_place_ids_cannot_merge_even_with_approval():
    candidates = [_candidate("a", place="p1"), _candidate("b", place="p2")]
    with pytest.raises(IdentityConflict):
        resolve_competitor_identities(
            candidates, [AliasReview(left_id="a", right_id="b", approved=True,
                                     reviewed_by="owner", evidence_ref="receipt")]
        )


def test_unapproved_alias_does_not_merge():
    a, b = _candidate("a"), _candidate("b")
    result = resolve_competitor_identities(
        [a, b], [AliasReview(left_id="a", right_id="b", approved=False,
                             reviewed_by="owner", evidence_ref="receipt")]
    )
    assert len(result.clusters) == 2


def test_duplicate_candidate_ids_rejected():
    with pytest.raises(ValueError, match="unique"):
        resolve_competitor_identities([_candidate("a"), _candidate("a")])


def test_unknown_alias_ids_rejected():
    with pytest.raises(ValueError, match="known candidates"):
        resolve_competitor_identities(
            [_candidate("a")], [AliasReview(left_id="a", right_id="b",
                                             approved=True, reviewed_by="owner",
                                             evidence_ref="review-1")]
        )


def test_hostname_normalization_and_credential_url_rejection():
    assert normalize_domain("HTTPS://WWW.Canyon.EXAMPLE/") == "canyon.example"
    assert normalize_domain("canyon.example") == "canyon.example"
    with pytest.raises(ValueError):
        normalize_domain("https://admin:password@canyon.example")


def test_identity_candidate_limit_requires_review_no_truncation():
    many = [_candidate(f"r{i}") for i in range(251)]
    with pytest.raises(MatrixLimitError, match="250"):
        resolve_competitor_identities(many)


@pytest.mark.parametrize("bad_site", [
    "javascript:alert(1)", "http://user:pass@canyon.example",
    "https://canyon.example/locations/north", "https://canyon.example/?x=1",
    "https://canyon.example:8443", "https://foo$bar.example",
    "https://-bad-.example", "https://127.0.0.1", "https://localhost",
    "https://verybad.123",
])
def test_invalid_host_and_path_scoped_business_sites_denied(bad_site):
    with pytest.raises((ValueError, ValidationError)):
        BusinessCandidate(
            id="x", provider_id="test", name="Example", city="Prescott",
            website_domain=bad_site, domain_verified=False,
            receipt_id="fake-fixture",
        )
    with pytest.raises(ValueError):
        normalize_domain(bad_site)


def test_explicit_alias_rejection_is_not_suggested_again():
    records = [_candidate("a"), _candidate("b")]
    result = resolve_competitor_identities(
        records, [AliasReview(left_id="a", right_id="b", approved=False,
                              reviewed_by="owner", evidence_ref="reviewed-2026")]
    )
    assert result.manual_review_pairs == ()


def test_known_distinct_place_ids_are_blocked_not_mergeable_review():
    records = [_candidate("a", place="place-a"),
               _candidate("b", place="place-b")]
    resolved = resolve_competitor_identities(records)
    assert resolved.manual_review_pairs == ()
    assert resolved.blocked_conflicts == (("a", "b", "distinct_provider_place_ids"),)


def test_imported_real_gsc_average_position_retains_owner_import_provenance():
    record = SourceObservation(
        cell_id="a"*20, request_signature="b"*64,
        provider_id="gsc", source=SearchSource.SEARCH_CONSOLE,
        observed_at=datetime.now(timezone.utc),
        provider_receipt_id="owner-csv-1",
        evidence_class=EvidenceClass.OWNER_IMPORT,
        presence=Presence.OBSERVED, gsc_average_position=2.7,
    )
    assert record.evidence_class == EvidenceClass.OWNER_IMPORT


@pytest.mark.parametrize("value", [0, 0.5])
def test_invalid_gsc_average_position_denied(value):
    with pytest.raises(ValidationError):
        SourceObservation(
            cell_id="a"*20, request_signature="b"*64,
            provider_id="gsc", source=SearchSource.SEARCH_CONSOLE,
            observed_at=datetime.now(timezone.utc),
            provider_receipt_id="csv", evidence_class=EvidenceClass.OWNER_IMPORT,
            presence=Presence.OBSERVED, gsc_average_position=value,
        )


def test_unobserved_or_synthetic_gsc_average_position_denied():
    for presence, source_class in (
        (Presence.UNKNOWN, EvidenceClass.OWNER_IMPORT),
        (Presence.OBSERVED, EvidenceClass.SYNTHETIC),
    ):
        with pytest.raises(ValidationError):
            SourceObservation(
                cell_id="a"*20, request_signature="b"*64,
                provider_id="gsc", source=SearchSource.SEARCH_CONSOLE,
                observed_at=datetime.now(timezone.utc),
                provider_receipt_id="csv", evidence_class=source_class,
                presence=presence, gsc_average_position=2.7,
            )


def test_bad_base_phrase_control_characters_denied():
    with pytest.raises(ValidationError):
        SearchPhrase(id="x", base_text="hello\nnewline",
                     variants=("example phrase",))


def test_no_live_provider_calls_and_only_plan_generation():
    import app.services.search_matrix as m
    source = inspect.getsource(m)
    assert "requests.get(" not in source
    assert "httpx.get(" not in source
    assert "urlopen(" not in source
    assert "selenium" not in source
