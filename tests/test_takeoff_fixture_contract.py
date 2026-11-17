"""MF-063: synthetic takeoff evidence contract; not a production plan recognition test.

The fixture lives as one JSON example in tests/fixtures/takeoff/README.md.
Everything here is offline and generated: no PDF, customer plan, supplier price,
third-party model or account integration is accessed.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

import pytest


FIXTURE_README = Path(__file__).parent / "fixtures" / "takeoff" / "README.md"


def load_fixture() -> dict:
    text = FIXTURE_README.read_text(encoding="utf-8")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    assert match is not None, "README must contain the canonical JSON specimen"
    return json.loads(match.group(1))


def _area(points: list[list[float]], scale: float) -> float:
    return abs(sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )) / 2 / (scale * scale)


def _perimeter(points: list[list[float]], scale: float) -> float:
    return sum(
        math.hypot(x2 - x1, y2 - y1)
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    ) / scale


def _valid_ring(points) -> bool:
    return (
        isinstance(points, list)
        and len(points) >= 3
        and all(
            isinstance(p, list)
            and len(p) == 2
            and all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                for v in p
            )
            for p in points
        )
        and len({tuple(p) for p in points}) >= 3
        and all(a != b for a, b in zip(points, points[1:] + points[:1]))
    )


def validate_fixture(record: dict) -> list[str]:
    """Check the *evidence contract* and arithmetic, not geometry recognition."""
    errors: list[str] = []
    if record.get("schema_version") != "mf.takeoff_fixture.v1":
        errors.append("schema_version")
    source = record.get("source", {})
    synthetic = source.get("source_kind") == "synthetic"
    if not synthetic and source.get("source_kind") not in ("licensed_real", "owner_authorized_real"):
        errors.append("source_kind")
    if synthetic:
        if source.get("rights_basis") != "generated_fixture":
            errors.append("rights_basis")
    else:
        if not source.get("owner_consent_recorded"):
            errors.append("owner_consent")
        if source.get("rights_basis") not in ("licensed", "owner_authorized"):
            errors.append("rights_basis")
        if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("source_sha256", ""))):
            errors.append("real_source_sha256")
    sheet = record.get("sheet", {})
    if not all(sheet.get(k) is not None for k in
               ("sheet_id", "revision_id", "page_index", "viewport_id", "document_kind")):
        errors.append("sheet_identity")
    if sheet.get("coordinate_unit") != "pdf_point":
        errors.append("coordinate_unit")
    scale = sheet.get("points_per_foot")
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or scale <= 0:
        errors.append("scale")
        return errors
    calibration = sheet.get("calibration", {})
    a, b = calibration.get("start_point_pdf_pt"), calibration.get("end_point_pdf_pt")
    physical = calibration.get("physical_distance_ft")
    if (not isinstance(a, list) or not isinstance(b, list)
        or len(a) != 2 or len(b) != 2 or
        not isinstance(physical, (int, float)) or physical <= 0):
        errors.append("calibration")
    elif not math.isclose(math.dist(a, b) / physical, scale, rel_tol=1e-8):
        errors.append("scale_conflict")
    if synthetic:
        if sheet.get("scale_status") != "verified_on_synthetic_fixture":
            errors.append("synthetic_scale")
    elif sheet.get("scale_status") != "verified_on_real_sheet" or (
            not calibration.get("human_confirmed")):
        errors.append("real_scale_approval")
    measure = record.get("measurement", {})
    reference = f'{sheet.get("sheet_id")}@{sheet.get("revision_id")}/{sheet.get("viewport_id")}'
    if measure.get("source_reference") != reference:
        errors.append("sheet_provenance")
    outer = measure.get("outer_ring_pdf_pt")
    holes = measure.get("holes", [])
    if not _valid_ring(outer) or not isinstance(holes, list) or (
            any(not _valid_ring(h.get("ring_pdf_pt")) for h in holes)):
        errors.append("polygon_geometry")
        return errors
    truth = measure.get("ground_truth", {})
    gross = _area(outer, scale)
    deducted = sum(_area(h["ring_pdf_pt"], scale) for h in holes)
    outer_perimeter = _perimeter(outer, scale)
    hole_perimeter = sum(_perimeter(h["ring_pdf_pt"], scale) for h in holes)
    for key, computed in (
        ("gross_area_sqft", gross),
        ("deduction_area_sqft", deducted),
        ("net_area_sqft", gross - deducted),
        ("external_perimeter_ft", outer_perimeter),
        ("hole_perimeter_ft", hole_perimeter),
    ):
        if not math.isclose(truth.get(key, float("nan")), computed, abs_tol=1e-7):
            errors.append("geometry_" + key)
    if deducted >= gross:
        errors.append("nonpositive_net_area")
    if any(h.get("scope_policy") != "deduct_for_this_flooring_fixture_only" or
           h.get("perimeter_policy") != "do_not_count_for_baseboard" for h in holes):
        errors.append("deduction_policy")
    if synthetic:
        if (measure.get("review_status") != "synthetic_only"
            or truth.get("independently_verified") is not False
            or truth.get("reviewer_reference") is not None):
            errors.append("fake_professional_approval")
    elif (measure.get("review_status") != "professionally_reviewed" or
          not truth.get("independently_verified") or
          not truth.get("reviewer_reference")):
        errors.append("missing_independent_reviewer")
    symbols = record.get("symbols", {})
    if symbols.get("matching_policy") != "one_to_one_spatial_class_aware":
        errors.append("symbol_matching_policy")
    observed_ids: set[str] = set()
    for symbol in symbols.get("ground_truth", []):
        bbox = symbol.get("bbox_pdf_pt")
        if (not isinstance(bbox, list) or len(bbox) != 4 or
            not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bbox) or
            bbox[0] >= bbox[2] or bbox[1] >= bbox[3] or
            not symbol.get("class") or not symbol.get("symbol_id") or
            symbol["symbol_id"] in observed_ids):
            errors.append("spatial_symbol_ground_truth")
            break
        observed_ids.add(symbol["symbol_id"])
    pricing = record.get("estimate_evidence", {})
    if synthetic:
        if pricing.get("price_status") != "synthetic_sample_only" or (
                pricing.get("contractor_approval_reference") is not None):
            errors.append("fake_price_approval")
    else:
        if pricing.get("price_status") == "professionally_verified" and (
            not pricing.get("contractor_approval_reference")
            or not pricing.get("price_observed_at")
            or pricing.get("labor_hours_are_formula_derived") is not True
        ):
            errors.append("unsubstantiated_cost_approval")
    return errors


def test_canonical_synthetic_evidence_contract_is_consistent():
    fixture = load_fixture()
    assert validate_fixture(fixture) == []
    assert fixture["measurement"]["ground_truth"]["net_area_sqft"] == 22.0
    assert fixture["source"]["source_kind"] == "synthetic"


@pytest.mark.parametrize(("section", "key", "value", "issue"), [
    ("sheet", "points_per_foot", 17.5, "scale_conflict"),
    ("sheet", "viewport_id", "VP-OTHER", "sheet_provenance"),
    ("sheet", "scale_status", "verified_on_real_sheet", "synthetic_scale"),
    ("measurement", "review_status", "professionally_reviewed", "fake_professional_approval"),
    ("symbols", "matching_policy", "count_totals_only", "symbol_matching_policy"),
    ("estimate_evidence", "price_status", "professionally_verified", "fake_price_approval"),
    ("source", "rights_basis", "unknown", "rights_basis"),
])
def test_contract_rejects_mutated_false_evidence(section, key, value, issue):
    item = copy.deepcopy(load_fixture())
    item[section][key] = value
    assert issue in validate_fixture(item)


def test_geometry_mutation_cannot_self_verify_its_own_area():
    item = copy.deepcopy(load_fixture())
    item["measurement"]["outer_ring_pdf_pt"][1][0] += 18
    assert "geometry_gross_area_sqft" in validate_fixture(item)


def test_real_source_cannot_pass_without_owner_license_and_human_ground_truth():
    item = copy.deepcopy(load_fixture())
    item["source"]["source_kind"] = "owner_authorized_real"
    errors = validate_fixture(item)
    assert "owner_consent" in errors
    assert "real_source_sha256" in errors
    assert "real_scale_approval" in errors
    assert "missing_independent_reviewer" in errors


def test_unreviewed_real_catalog_cannot_be_marked_professionally_verified():
    item = copy.deepcopy(load_fixture())
    pricing = item["estimate_evidence"]
    item["source"].update({
        "source_kind": "licensed_real",
        "rights_basis": "licensed",
        "owner_consent_recorded": True,
        "source_sha256": "a" * 64,
    })
    item["sheet"]["scale_status"] = "verified_on_real_sheet"
    item["sheet"]["calibration"]["human_confirmed"] = True
    item["measurement"]["review_status"] = "professionally_reviewed"
    item["measurement"]["ground_truth"].update({
        "independently_verified": True, "reviewer_reference": "synthetic-reviewer-test-1",
    })
    pricing["price_status"] = "professionally_verified"
    errors = validate_fixture(item)
    assert "unsubstantiated_cost_approval" in errors
    pricing.update({
        "contractor_approval_reference": "synthetic-approval-test-1",
        "price_observed_at": "2026-11-17T00:00:00Z",
        "labor_hours_are_formula_derived": True,
    })
    assert validate_fixture(item) == []
