"""Bounded browser review evidence validation; no production data."""
import copy
import pytest
from app.services import takeoff_share as share


def packet():
    return {
        "schema": share.SCHEMA, "mode": "contractor_opt_in_read_only", "as_of": "2026-11-25",
        "project": {"scope": "bathroom", "code_basis": None, "project_name_shared": False,
                    "original_pdf_shared": False, "raw_geometry_shared": False, "cost_amounts_shared": False},
        "export_manifest": {"counts": {"measurements": {"source": 1, "exported": 1}},
                            "truncated": [], "complete": True, "unresolved_material_count": 1},
        "drawing_register": [{"id": "sheet_1", "document": "document_1", "page": 1}],
        "printed_work_areas": [], "scope_items": [{"id": "scope_1", "title": "Floor tile",
            "evidence": [{"document": "document_1", "page": 1, "printed_text": "Fictional room"}]}],
        "measurements": [{"id": "m1", "scope_item_id": "scope_1", "document": "document_1",
            "page": 1, "measured_quantity": 64, "unit": "SF", "scale_status": "verified"}],
        "field_entered_quantities": [],
        "material_lines": [{"line_id": "line_1", "quantity": 70.4, "unit": "SF",
                            "measurement_ids": ["m1"], "price_status": "UNKNOWN", "needs_price": True}],
        "price_research_queue": [{"line_id": "line_1", "description": "Tile", "quantity": 70.4}],
        "warnings": [], "review_questions": ["Untrusted input omitted"],
        "local_skill_assurance": {"schema": "mf.takeoff.skill-assurance/1",
            "skill": "estimate-assurance", "runtime": "mf.skill-assurance.browser/1",
            "readiness": "blocked", "findings": [{"code": "PRICE_NOT_CONFIRMED", "severity": "block",
                                                  "ids": ["line_1"]}], "dispositions": [], "limitations": []},
    }


def test_review_evidence_preserved_without_arbitrary_nested_objects():
    p = packet()
    p["project"]["code_basis"] = {"private": "not forwarded"}
    clean = share.validate_packet(p)
    assert clean["measurements"] == p["measurements"]
    assert clean["local_skill_assurance"]["findings"] == p["local_skill_assurance"]["findings"]
    assert "code_basis" not in clean["project"]
    assert "review_questions" not in clean


@pytest.mark.parametrize("change", [
    lambda p: p.update(pdf="secret"),
    lambda p: p["project"].update(raw_geometry_shared=True),
    lambda p: p["measurements"][0].update(points=[[1,2]]),
    lambda p: p["material_lines"][0].update(extended_cents=100),
    lambda p: p["scope_items"][0]["evidence"][0].update(secret="x"),
    lambda p: p["measurements"][0].update(measured_quantity=float("nan")),
    lambda p: p["measurements"][0].update(measured_quantity=10**100),
    lambda p: p["measurements"][0].update(id="../bad"),
    lambda p: p["scope_items"][0].update(title="x"*321),
    lambda p: p.update(measurements=[p["measurements"][0]]*251),
    lambda p: p.update(as_of="not-a-date"),
])
def test_rejects_unsafe_or_oversize_fields(change):
    p=copy.deepcopy(packet())
    change(p)
    with pytest.raises(share.ShareError):
        share.validate_packet(p)


def test_incomplete_manifest_cannot_claim_readiness():
    p = packet()
    p["local_skill_assurance"]["readiness"] = "review_only"
    p["local_skill_assurance"]["findings"] = []
    clean = share.validate_packet(p)
    assert clean["export_manifest"]["complete"] is False
    assert clean["local_skill_assurance"]["readiness"] == "blocked"
    p["local_skill_assurance"]["readiness"] = "approved"
    with pytest.raises(share.ShareError):
        share.validate_packet(p)

def test_shared_packet_drops_free_text_plan_excerpts():
    p = packet()
    p["scope_items"][0]["title"] = "PRIVATE CUSTOMER NAME"
    p["scope_items"][0]["evidence"][0]["printed_text"] = "PRIVATE ADDRESS"
    p["warnings"] = [{"code":"REVIEW", "message":"PRIVATE PHONE"}]
    p["material_lines"][0]["description"] = "PRIVATE CUSTOMER NOTE"
    result = share.validate_packet(p)
    assert "PRIVATE" not in repr(result)
    assert result["scope_items"][0]["evidence"][0]["document"] == "document_1"
    assert result["warnings"] == [{"code":"REVIEW"}]

def test_unknown_skill_severity_fails_closed():
    p = packet()
    p["local_skill_assurance"]["findings"][0]["severity"] = "invented"
    with pytest.raises(share.ShareError):
        share.validate_packet(p)
