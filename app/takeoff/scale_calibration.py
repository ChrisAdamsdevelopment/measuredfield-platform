"""MF-064: explicit dimension, per-viewport PDF scale calibration."""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Literal
from .measurement_schema import DrawingRef

Point = tuple[float, float]
ScaleState = Literal["verified", "review_required"]

class ScaleConflict(ValueError):
    """Scale evidence conflicts or mixes sheet/revision/viewport identities."""

def _point(value: Point) -> Point:
    if (not isinstance(value, (tuple, list)) or len(value) != 2 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise ValueError("Expected two finite drawing coordinates")
    return float(value[0]), float(value[1])

@dataclass(frozen=True)
class DimensionEvidence:
    reference: DrawingRef
    evidence_id: str
    start_pdf_pt: Point
    end_pdf_pt: Point
    physical_distance_ft: float
    human_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise ValueError("Dimension evidence ID required")
        _point(self.start_pdf_pt)
        _point(self.end_pdf_pt)
        if (type(self.physical_distance_ft) not in (float, int) or
                not math.isfinite(self.physical_distance_ft) or self.physical_distance_ft <= 0):
            raise ValueError("Physical distance must be positive feet")
        if math.dist(self.start_pdf_pt, self.end_pdf_pt) <= 0:
            raise ValueError("Dimension endpoints must differ")
        if type(self.human_confirmed) is not bool:
            raise ValueError("human_confirmed must be explicit boolean")

    @property
    def points_per_foot(self) -> float:
        return math.dist(self.start_pdf_pt, self.end_pdf_pt) / self.physical_distance_ft

@dataclass(frozen=True)
class ViewportCalibration:
    reference: DrawingRef
    points_per_foot: float | None
    state: ScaleState
    dimension_ids: tuple[str, ...]
    issue: str | None = None

    def require_verified(self, reference: DrawingRef) -> float:
        if reference != self.reference:
            raise ScaleConflict("Different sheet, revision or viewport needs its own scale")
        if self.state != "verified" or self.points_per_foot is None:
            raise ScaleConflict("Scale needs independent dimensions and human verification")
        return self.points_per_foot

def calibrate_viewport(
    reference: DrawingRef,
    evidence: list[DimensionEvidence] | tuple[DimensionEvidence, ...],
    *,
    relative_tolerance: float = 0.005,
) -> ViewportCalibration:
    """Verify only two distinct consistent dimensions, both human confirmed."""
    if (type(relative_tolerance) not in (int, float) or
            not math.isfinite(relative_tolerance) or not 0 < relative_tolerance <= 0.02):
        raise ValueError("Calibration tolerance must be finite and small")
    if not isinstance(evidence, (list, tuple)):
        raise ValueError("Provide dimension evidence records")
    if not evidence:
        return ViewportCalibration(reference, None, "review_required", (), "no_dimension_evidence")
    ids: list[str] = []
    ratios: list[float] = []
    for item in evidence:
        if not isinstance(item, DimensionEvidence):
            raise ValueError("Expected structured dimension evidence")
        if item.reference != reference:
            raise ScaleConflict("Dimension belongs to a different sheet or viewport")
        if item.evidence_id in ids:
            raise ScaleConflict("Duplicate calibration dimension ID")
        ids.append(item.evidence_id)
        ratios.append(item.points_per_foot)
    baseline = ratios[0]
    if any(not math.isclose(r, baseline, rel_tol=relative_tolerance) for r in ratios[1:]):
        raise ScaleConflict("Inconsistent dimension scales; estimator review required")
    if len(ratios) < 2 or not all(item.human_confirmed for item in evidence):
        return ViewportCalibration(reference, baseline, "review_required", tuple(ids),
                                   "independent_human_dimensions_required")
    return ViewportCalibration(reference, math.fsum(ratios) / len(ratios),
                               "verified", tuple(ids))
