"""MF-064 local geometry and source provenance tests.

Synthetic drawing vectors and mocked PDF parser only. They demonstrate math
and fail-closed behavior, not professional blueprint-recognition accuracy.
"""
from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace

import pytest

from app.takeoff.measurement_schema import DrawingRef, MeasurementReview, PlanPermission
from app.takeoff.scale_calibration import (
    DimensionEvidence, ScaleConflict, calibrate_viewport,
)
from app.takeoff.geometry import (
    InvalidGeometry, inspect_open_path, measure_area, measure_line,
)
from app.takeoff.pdf_ingestion import (
    PdfIntakeError, PdfParserUnavailable, inspect_pdf, MAX_PDF_BYTES,
)

HASH = hashlib.sha256(b"approved synthetic source").hexdigest()
SAMPLE_PDF = b"%PDF-1.7\n" + b"0" * 30 + b"\n%%EOF\n"


def _ref(*, viewport: str = "V1", revision: str = "R1") -> DrawingRef:
    return DrawingRef(HASH, "A-101", revision, 0, viewport)


def _evidence(ref, label, start, end, feet, confirmed=True):
    return DimensionEvidence(ref, label, start, end, feet, confirmed)


def _verified(ref: DrawingRef):
    return calibrate_viewport(
        ref,
        [
            _evidence(ref, "dimension-6ft", (0, 0), (108, 0), 6),
            _evidence(ref, "dimension-4ft", (0, 0), (0, 72), 4),
        ],
    )


def _rings():
    outer = ((36, 72), (144, 72), (144, 144), (36, 144))
    hole = ((72, 90), (108, 90), (108, 108), (72, 108))
    return outer, hole


def test_calibration_requires_distinct_consistent_human_evidence():
    ref = _ref()
    verified = _verified(ref)
    assert verified.state == "verified"
    assert verified.require_verified(ref) == pytest.approx(18)
    assert verified.dimension_ids == ("dimension-6ft", "dimension-4ft")
    just_one = calibrate_viewport(ref, [
        _evidence(ref, "only", (0, 0), (108, 0), 6)
    ])
    assert just_one.state == "review_required"
    with pytest.raises(ScaleConflict):
        just_one.require_verified(ref)


@pytest.mark.parametrize("second_feet", [3, 5, 8, 0.01])
def test_wrong_dimensions_cannot_silently_be_averaged(second_feet):
    ref = _ref()
    with pytest.raises(ScaleConflict):
        calibrate_viewport(ref, [
            _evidence(ref, "one", (0, 0), (108, 0), 6),
            _evidence(ref, "two", (0, 0), (0, 72), second_feet),
        ])


def test_mixed_viewport_revision_and_duplicate_dimension_rejected():
    ref = _ref()
    other = _ref(viewport="V2")
    with pytest.raises(ScaleConflict):
        calibrate_viewport(ref, [
            _evidence(ref, "d1", (0, 0), (108, 0), 6),
            _evidence(other, "d2", (0, 0), (0, 72), 4),
        ])
    with pytest.raises(ScaleConflict):
        calibrate_viewport(ref, [
            _evidence(ref, "same", (0, 0), (108, 0), 6),
            _evidence(ref, "same", (0, 0), (0, 72), 4),
        ])
    with pytest.raises(ScaleConflict):
        _verified(ref).require_verified(_ref(revision="R2"))


def test_unreviewed_scale_and_nonhuman_confirmation_remain_pending():
    ref = _ref()
    assert calibrate_viewport(ref, []).state == "review_required"
    partly = calibrate_viewport(ref, [
        _evidence(ref, "first", (0, 0), (108, 0), 6),
        _evidence(ref, "second", (0, 0), (0, 72), 4, False),
    ])
    assert partly.state == "review_required"


def test_area_with_explicit_hole_is_deterministic_and_source_linked():
    ref = _ref()
    outer, hole = _rings()
    area = measure_area(ref, _verified(ref), measurement_id="floor-1",
                        outer_ring=outer, holes=[hole])
    assert area.gross_area_sqft == pytest.approx(24.0)
    assert area.deduction_area_sqft == pytest.approx(2.0)
    assert area.net_area_sqft == pytest.approx(22.0)
    assert area.external_perimeter_ft == pytest.approx(20.0)
    assert area.hole_perimeter_ft == pytest.approx(6.0)
    assert area.hole_count == 1
    assert area.outer_ring_pdf_pt == outer
    assert area.holes_pdf_pt == (hole,)
    assert area.reference == ref
    assert area.review.status == "review_required"


def test_polygon_orientation_and_explicit_closing_vertex_do_not_change_area():
    ref = _ref()
    outer, hole = _rings()
    original = measure_area(ref, _verified(ref), measurement_id="x", outer_ring=outer,
                            holes=[hole])
    reversed_area = measure_area(
        ref, _verified(ref), measurement_id="x",
        outer_ring=tuple(reversed(outer)),
        holes=[tuple(reversed(hole))],
    )
    assert reversed_area.net_area_sqft == original.net_area_sqft


@pytest.mark.parametrize("bad_outer", [
    ((0, 0), (10, 10), (0, 10), (10, 0)),
    ((0, 0), (10, 0), (10, 0), (0, 10)),
    ((0, 0), (10, 0), (20, 0)),
    ((0, 0), (math.inf, 0), (0, 10)),
])
def test_invalid_or_self_intersecting_boundary_rejected(bad_outer):
    ref = _ref()
    with pytest.raises(InvalidGeometry):
        measure_area(ref, _verified(ref), measurement_id="bad", outer_ring=bad_outer)


@pytest.mark.parametrize("hole", [
    ((35, 75), (60, 75), (60, 85), (35, 85)),  # crosses shell
    ((36, 80), (45, 80), (45, 90), (36, 90)),  # touches shell
    ((150, 100), (165, 100), (165, 110), (150, 110)),  # outside
])
def test_unsafe_deduction_never_subtracted(hole):
    ref = _ref()
    outer, _ = _rings()
    with pytest.raises(InvalidGeometry):
        measure_area(ref, _verified(ref), measurement_id="x",
                     outer_ring=outer, holes=[hole])


def test_overlapping_or_nested_holes_rejected():
    ref = _ref()
    outer, hole = _rings()
    with pytest.raises(InvalidGeometry):
        measure_area(ref, _verified(ref), measurement_id="nested", outer_ring=outer,
                     holes=[hole, ((75, 93), (81, 93), (81, 99), (75, 99))])
    with pytest.raises(InvalidGeometry):
        measure_area(ref, _verified(ref), measurement_id="overlap", outer_ring=outer,
                     holes=[hole, ((90, 90), (117, 90), (117, 108), (90, 108))])


def test_line_length_and_open_path_are_not_automatically_gap_healed():
    ref = _ref()
    scale = _verified(ref)
    line = measure_line(ref, scale, measurement_id="baseboard",
                        points=[(0, 0), (108, 0), (108, 72)])
    assert line.length_ft == pytest.approx(10.0)
    assert line.points_pdf_pt == ((0.0, 0.0), (108.0, 0.0), (108.0, 72.0))
    open_path = inspect_open_path(
        ref, scale, measurement_id="room-path",
        points=[(0, 0), (108, 0), (108, 72), (0, 72), (18, 0)],
    )
    assert open_path.status == "review_required"
    assert open_path.gap_pdf_pt == pytest.approx(18)
    assert open_path.gap_ft == pytest.approx(1)
    assert inspect_open_path(
        ref, scale, measurement_id="closed",
        points=[(0, 0), (108, 0), (108, 72), (0, 0)],
    ) is None


def test_apparent_approval_requires_real_reviewer_audit_reference():
    with pytest.raises(ValueError):
        MeasurementReview("human_verified")
    assert MeasurementReview().status == "review_required"
    assert MeasurementReview("human_verified", "estimator-1", "signed-review-event-1")


def test_plan_permission_and_source_ref_must_be_explicit():
    with pytest.raises(ValueError):
        PlanPermission("owner_authorized", "", "")
    with pytest.raises(ValueError):
        DrawingRef("dummy", "A-101", "R1", 0, "V1")
    assert PlanPermission("synthetic", "fixture-author", "generated-fixture-1")


class _Pages:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        return False


def _page(*, vectors=0, images=0, chars=0, width=612, height=792):
    return SimpleNamespace(
        width=width, height=height, lines=[{}] * vectors,
        rects=[], curves=[], images=[{}] * images, chars=[{}] * chars
    )


def _fake_parser(*pages):
    return lambda stream: _Pages(list(pages))


def _permission():
    return PlanPermission("synthetic", "test-operator", "synthetic-pdf-1")


def test_pdf_intake_uses_real_parser_contract_not_pdf_name_guessing():
    result = inspect_pdf(
        SAMPLE_PDF, permission=_permission(),
        parser_factory=_fake_parser(
            _page(vectors=1), _page(images=1), _page(vectors=2, images=1),
            _page(chars=20),
        ),
    )
    assert [page.kind for page in result.pages] == [
        "vector", "raster", "mixed", "unknown",
    ]
    assert result.size_bytes == len(SAMPLE_PDF)
    assert result.source_sha256 == hashlib.sha256(SAMPLE_PDF).hexdigest()
    assert result.drawing_ref(
        sheet_id="A-101", revision_id="R2", page_index=1, viewport_id="V1"
    ).page_index == 1
    with pytest.raises(ValueError):
        result.drawing_ref(sheet_id="A", revision_id="R", page_index=22, viewport_id="V")


@pytest.mark.parametrize("payload", [
    b"not a pdf but has %%EOF",
    b"%PDF-1.7\n/Encrypt\n" + b"x" * 12 + b"%%EOF",
    b"%PDF-1.7\n/JavaScript\n" + b"x" * 12 + b"%%EOF",
    b"%PDF-1.7\n/EmbeddedFile\n" + b"x" * 12 + b"%%EOF",
    b"%PDF-1.7\n" + b"z" * 30,
])
def test_pdf_intake_rejects_invalid_or_dangerous_input(payload):
    with pytest.raises(PdfIntakeError):
        inspect_pdf(payload, permission=_permission(), parser_factory=_fake_parser(_page()))


def test_pdf_intake_denies_nonapproved_and_unbounded_input():
    with pytest.raises(PdfIntakeError):
        inspect_pdf(SAMPLE_PDF, permission=None, parser_factory=_fake_parser(_page()))
    with pytest.raises(PdfIntakeError):
        inspect_pdf(b"%PDF-1.7\n" + b" " * (MAX_PDF_BYTES + 1) + b"%%EOF",
                    permission=_permission(), parser_factory=_fake_parser(_page()))
    with pytest.raises(PdfIntakeError):
        inspect_pdf(SAMPLE_PDF, permission=_permission(),
                    parser_factory=_fake_parser(*[_page()] * 101))
    with pytest.raises(PdfIntakeError):
        inspect_pdf(SAMPLE_PDF, permission=_permission(),
                    parser_factory=_fake_parser(_page(width=1e8)))


def test_parser_must_be_present_not_a_regex_fallback(monkeypatch):
    import app.takeoff.pdf_ingestion as intake
    monkeypatch.setattr(
        intake, "_pdfplumber_factory",
        lambda stream: (_ for _ in ()).throw(PdfParserUnavailable("not installed")),
    )
    with pytest.raises(PdfParserUnavailable):
        inspect_pdf(SAMPLE_PDF, permission=_permission())


def test_parser_failure_is_sanitized_and_fails_closed():
    def explode(stream):
        raise RuntimeError("private customer filename must not be displayed")
    with pytest.raises(PdfIntakeError, match="could not safely inspect") as exc:
        inspect_pdf(SAMPLE_PDF, permission=_permission(), parser_factory=explode)
    assert "private customer filename" not in str(exc.value)
