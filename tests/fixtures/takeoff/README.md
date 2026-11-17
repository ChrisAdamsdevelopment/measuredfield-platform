# MF-063 — Construction Takeoff Evidence Fixture Contract (v1)

This folder holds **no customer architectural plans**. The JSON below is a fully synthetic example for designing automated validators. Do not treat a synthetic geometry calculation as evidence that MeasuredField can recognize rooms on a real drawing.

Every real-world benchmark fixture must be registered only after explicit document rights, owner permission, and estimator ground-truth review. No such real fixtures are included or approved here. Where redistribution isn't allowed, store only redacted metadata and restricted fixture references, not plans in Git.

## Required fixture categories

- Synthetic vector geometry: deterministic calculation smoke test, **not a measurement-accuracy benchmark**.
- Licensed/owner-authorized **real** vector plans: expert ground truth independent of algorithm output.
- Licensed/owner-authorized **real** scanned/raster plans: calibration/deskew/quality and human corrections.
- Multi-viewport, multi-revision, rotated, mixed-scale, low-DPI, partially obscured, symbol-decoy and open-wall negative fixtures.
- Scale conflict is a **hard blocker**; resolution requires sheet-specific, viewport-specific dimension evidence and human review.
- Each source item carries plan license and consent status, content hash, source sheet/revision, viewport/page coordinate transform and a recorded reviewer; no unverified "APPROVED" labels.

## Canonical example: synthetic only

The sample is a 6 ft × 4 ft room on a vector PDF using 18 PDF points per real foot (1/4 inch = 1 foot). A 2 ft × 1 ft illustrative exclusion is deducted **for this sample flooring scope only**. Its 6-foot inside boundary is explicitly *not* added to baseboard. No claim is made that a real kitchen island universally requires flooring exclusion.

```json
{
  "schema_version": "mf.takeoff_fixture.v1",
  "fixture_id": "synthetic-room-with-exclusion-v1",
  "source": {
    "source_kind": "synthetic",
    "rights_basis": "generated_fixture",
    "redistribution_allowed": true,
    "owner_consent_recorded": false,
    "source_sha256": "synthetic-placeholder-not-a-real-document-hash",
    "redaction_status": "not_applicable"
  },
  "sheet": {
    "sheet_id": "A-101-SYNTHETIC",
    "revision_id": "SYNTH-R1",
    "page_index": 0,
    "viewport_id": "VP-1",
    "document_kind": "vector_pdf",
    "rotation_degrees": 0,
    "coordinate_unit": "pdf_point",
    "scale_status": "verified_on_synthetic_fixture",
    "points_per_foot": 18.0,
    "calibration": {
      "kind": "known_dimension",
      "start_point_pdf_pt": [36.0, 72.0],
      "end_point_pdf_pt": [144.0, 72.0],
      "physical_distance_ft": 6.0,
      "human_confirmed": false
    }
  },
  "measurement": {
    "measurement_id": "FLOOR-A1",
    "trade": "flooring",
    "geometry_type": "polygon_with_holes",
    "outer_ring_pdf_pt": [[36.0, 72.0], [144.0, 72.0], [144.0, 144.0], [36.0, 144.0]],
    "holes": [
      {
        "hole_id": "EXCLUSION-1",
        "ring_pdf_pt": [[72.0, 90.0], [108.0, 90.0], [108.0, 108.0], [72.0, 108.0]],
        "scope_policy": "deduct_for_this_flooring_fixture_only",
        "perimeter_policy": "do_not_count_for_baseboard",
        "review_required_on_real_plan": true
      }
    ],
    "ground_truth": {
      "gross_area_sqft": 24.0,
      "deduction_area_sqft": 2.0,
      "net_area_sqft": 22.0,
      "external_perimeter_ft": 20.0,
      "hole_perimeter_ft": 6.0,
      "independently_verified": false,
      "reviewer_reference": null
    },
    "review_status": "synthetic_only",
    "source_reference": "A-101-SYNTHETIC@SYNTH-R1/VP-1"
  },
  "symbols": {
    "ground_truth": [
      {"symbol_id": "R1", "class": "receptacle", "bbox_pdf_pt": [44.0, 77.0, 49.0, 82.0]},
      {"symbol_id": "R2", "class": "receptacle", "bbox_pdf_pt": [120.0, 125.0, 125.0, 130.0]},
      {"symbol_id": "D1", "class": "smoke_detector_decoy", "bbox_pdf_pt": [165.0, 75.0, 171.0, 81.0]}
    ],
    "matching_policy": "one_to_one_spatial_class_aware",
    "matching_tolerance_pdf_pt": 3.0
  },
  "estimate_evidence": {
    "price_status": "synthetic_sample_only",
    "catalog_source": "hardcoded_demo_values",
    "price_observed_at": null,
    "contractor_approval_reference": null,
    "labor_hours_are_formula_derived": false,
    "known_blocker": "original_r2_xlsx_P5_P20_static_labor_hours"
  }
}
```

## Test assertions

- 6 ft × 4 ft outer polygon = 24 sq ft gross; hole = 2 sq ft; net = **22 sq ft**, outer perimeter **20 ft**, hole perimeter **6 ft**.
- Calibration line 108 PDF points / 6 ft = 18 points/ft; a conflicting scale must stop processing, not quietly use another viewport.
- Report flooring net area; do **not** add the inner 6-ft hole boundary to baseboard unless a separate approved trim rule exists.
- Ground truth symbols have spatial bounding boxes and classes; metric scoring uses one-to-one class-aware matches, not only equality of total counts.
- Never mark a synthetic fixture's reviewer/approval status as professional or a sample price as sourced.
- On real plans, an independent human label/reference must exist before counting that sheet toward the blind benchmark.
- Unreadable, overlapping or incompatible viewport/revision geometry must remain **review_required**, not a nominal zero-error result.

The full research and accuracy policy is in `docs/MF063_TAKEOFF_VALIDATION_CONTRACT.md`. Tests load this embedded JSON and check the synthetic arithmetic, scale consistency, source labels and mutation rejection. No PDF image, proprietary plan or vendor API data is accessed by these tests.
