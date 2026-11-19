"""MF-064: deterministic planar measurement of human-traced PDF geometry.

Coordinates are PDF points; length/area are computed from explicit,
independently calibrated dimensions, never from model-guessed coordinates.
Ambiguous open paths are returned for human correction, never gap-healed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from .measurement_schema import DrawingRef, MeasurementReview
from .scale_calibration import ViewportCalibration, _point

Point = tuple[float, float]
GEOMETRY_EPS = 1e-8  # PDF points; this is not a real-world accuracy guarantee.


class InvalidGeometry(ValueError):
    """Invalid, intersecting or unsupported 2D takeoff geometry."""


def _vertices(points: Sequence[Point], *, minimum: int = 3) -> tuple[Point, ...]:
    if not isinstance(points, (tuple, list)) or len(points) < minimum:
        raise InvalidGeometry("Geometry has too few vertices")
    try:
        vertices = tuple(_point(point) for point in points)
    except (TypeError, ValueError) as exc:
        raise InvalidGeometry("Nonfinite or invalid vertex") from exc
    if vertices[0] == vertices[-1] and len(vertices) > minimum:
        vertices = vertices[:-1]
    if len(vertices) < minimum:
        raise InvalidGeometry("Geometry has too few distinct vertices")
    if len(set(vertices)) != len(vertices):
        raise InvalidGeometry("Duplicated vertices or self-touching geometry")
    return vertices


def _pairs(points: tuple[Point, ...]) -> list[tuple[Point, Point]]:
    return [(points[i], points[(i + 1) % len(points)]) for i in range(len(points))]


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(point: Point, a: Point, b: Point) -> bool:
    if abs(_orientation(a, b, point)) > GEOMETRY_EPS:
        return False
    return (min(a[0], b[0]) - GEOMETRY_EPS <= point[0] <=
            max(a[0], b[0]) + GEOMETRY_EPS and
            min(a[1], b[1]) - GEOMETRY_EPS <= point[1] <=
            max(a[1], b[1]) + GEOMETRY_EPS)


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    aa = _orientation(a, b, c)
    ab = _orientation(a, b, d)
    ca = _orientation(c, d, a)
    cb = _orientation(c, d, b)
    if ((aa > GEOMETRY_EPS and ab < -GEOMETRY_EPS or
         aa < -GEOMETRY_EPS and ab > GEOMETRY_EPS) and
        (ca > GEOMETRY_EPS and cb < -GEOMETRY_EPS or
         ca < -GEOMETRY_EPS and cb > GEOMETRY_EPS)):
        return True
    return (abs(aa) <= GEOMETRY_EPS and _on_segment(c, a, b) or
            abs(ab) <= GEOMETRY_EPS and _on_segment(d, a, b) or
            abs(ca) <= GEOMETRY_EPS and _on_segment(a, c, d) or
            abs(cb) <= GEOMETRY_EPS and _on_segment(b, c, d))


def _shoelace(points: tuple[Point, ...]) -> float:
    return abs(math.fsum(
        x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in _pairs(points)
    )) / 2


def _perimeter(points: tuple[Point, ...]) -> float:
    return math.fsum(math.dist(a, b) for a, b in _pairs(points))


def _validate_ring(points: Sequence[Point]) -> tuple[Point, ...]:
    v = _vertices(points)
    if any(math.dist(a, b) <= GEOMETRY_EPS for a, b in _pairs(v)):
        raise InvalidGeometry("Zero-length polygon edge")
    if _shoelace(v) <= GEOMETRY_EPS:
        raise InvalidGeometry("Zero-area polygon")
    segments = _pairs(v)
    for i, (a, b) in enumerate(segments):
        for j in range(i + 1, len(segments)):
            # Adjacent segments are expected to share one endpoint.
            if j == i + 1 or (i == 0 and j == len(segments) - 1):
                continue
            if _segments_intersect(a, b, *segments[j]):
                raise InvalidGeometry("Self-intersecting or touching polygon")
    return v


def _point_inside(point: Point, ring: tuple[Point, ...]) -> bool:
    """Strict interior; boundary points do not count as contained."""
    for a, b in _pairs(ring):
        if _on_segment(point, a, b):
            return False
    x, y = point
    crossings = False
    for (x1, y1), (x2, y2) in _pairs(ring):
        if (y1 > y) != (y2 > y) and x < (
            (x2 - x1) * (y - y1) / (y2 - y1) + x1
        ):
            crossings = not crossings
    return crossings


def _any_edge_cross(ring1: tuple[Point, ...], ring2: tuple[Point, ...]) -> bool:
    return any(_segments_intersect(a, b, c, d)
               for a, b in _pairs(ring1) for c, d in _pairs(ring2))


@dataclass(frozen=True)
class AreaMeasurement:
    reference: DrawingRef
    measurement_id: str
    gross_area_sqft: float
    deduction_area_sqft: float
    net_area_sqft: float
    external_perimeter_ft: float
    hole_perimeter_ft: float
    hole_count: int
    outer_ring_pdf_pt: tuple[Point, ...]
    holes_pdf_pt: tuple[tuple[Point, ...], ...]
    review: MeasurementReview


@dataclass(frozen=True)
class LengthMeasurement:
    reference: DrawingRef
    measurement_id: str
    length_ft: float
    points_pdf_pt: tuple[Point, ...]
    review: MeasurementReview


@dataclass(frozen=True)
class OpenBoundary:
    reference: DrawingRef
    measurement_id: str
    gap_pdf_pt: float
    gap_ft: float | None
    status: str = "review_required"
    issue: str = "open_boundary_no_auto_healing"


def _measurement_id(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 128:
        raise ValueError("Measurement ID is required")
    return value


def measure_area(
    reference: DrawingRef,
    calibration: ViewportCalibration,
    *,
    measurement_id: str,
    outer_ring: Sequence[Point],
    holes: Sequence[Sequence[Point]] = (),
    review: MeasurementReview | None = None,
) -> AreaMeasurement:
    """Calculate net area/outer perimeter, with explicit trade-approved holes.

    Holes must be strictly within shell and disjoint. Their perimeters are
    recorded separately: they NEVER automatically become baseboard quantities.
    """
    scale = calibration.require_verified(reference)
    _measurement_id(measurement_id)
    shell = _validate_ring(outer_ring)
    if not isinstance(holes, (tuple, list)):
        raise InvalidGeometry("Holes must be explicit polygons")
    exclusions = [_validate_ring(h) for h in holes]
    for hole in exclusions:
        if not all(_point_inside(vertex, shell) for vertex in hole):
            raise InvalidGeometry("A deduction extends outside or touches its shell")
        if _any_edge_cross(hole, shell):
            raise InvalidGeometry("Deduction intersects its shell")
    for i, first in enumerate(exclusions):
        for second in exclusions[i + 1:]:
            if (_any_edge_cross(first, second) or
                    _point_inside(first[0], second) or
                    _point_inside(second[0], first)):
                raise InvalidGeometry("Deduction polygons intersect or overlap")
    gross_pt = _shoelace(shell)
    deduction_pt = math.fsum(_shoelace(hole) for hole in exclusions)
    if deduction_pt >= gross_pt:
        raise InvalidGeometry("Deducted area is not smaller than the original area")
    return AreaMeasurement(
        reference=reference,
        measurement_id=measurement_id,
        gross_area_sqft=gross_pt / scale**2,
        deduction_area_sqft=deduction_pt / scale**2,
        net_area_sqft=(gross_pt - deduction_pt) / scale**2,
        external_perimeter_ft=_perimeter(shell) / scale,
        hole_perimeter_ft=math.fsum(_perimeter(h) for h in exclusions) / scale,
        hole_count=len(exclusions),
        outer_ring_pdf_pt=shell,
        holes_pdf_pt=tuple(exclusions),
        review=review or MeasurementReview(),
    )


def measure_line(
    reference: DrawingRef,
    calibration: ViewportCalibration,
    *,
    measurement_id: str,
    points: Sequence[Point],
    review: MeasurementReview | None = None,
) -> LengthMeasurement:
    scale = calibration.require_verified(reference)
    _measurement_id(measurement_id)
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        raise InvalidGeometry("A measurement line requires at least two points")
    try:
        v = tuple(_point(p) for p in points)
    except (TypeError, ValueError) as exc:
        raise InvalidGeometry("Invalid measurement line point") from exc
    segments = [math.dist(a, b) for a, b in zip(v, v[1:])]
    if any(length <= GEOMETRY_EPS for length in segments):
        raise InvalidGeometry("Zero-length line segment")
    return LengthMeasurement(
        reference, measurement_id, math.fsum(segments) / scale,
        v, review or MeasurementReview()
    )


def inspect_open_path(
    reference: DrawingRef,
    calibration: ViewportCalibration,
    *,
    measurement_id: str,
    points: Sequence[Point],
) -> OpenBoundary | None:
    """Give an estimator the physical gap. Never silently close an open path."""
    _measurement_id(measurement_id)
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise InvalidGeometry("Path must have three or more points")
    try:
        v = tuple(_point(p) for p in points)
    except (TypeError, ValueError) as exc:
        raise InvalidGeometry("Invalid path point") from exc
    gap = math.dist(v[0], v[-1])
    if gap <= GEOMETRY_EPS:
        return None
    scale = calibration.points_per_foot if calibration.reference == reference else None
    return OpenBoundary(reference, measurement_id, gap, gap / scale if scale else None)
