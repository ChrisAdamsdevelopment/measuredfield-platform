// MeasuredField Remodel Takeoff — geometry.mjs (spec §3.2)
//
// Pure plan-geometry logic: scale calibration, calibration checks, and
// length / area / count measurement of traced shapes.
//
// Coordinate convention: every point is an UNROTATED PDF user-space point
// ({x, y}, 1 pt = 1/72 in) on a single page. Display rotation and zoom are the
// viewer's concern and must be undone before points reach this module.
//
// This module never invents a scale: a length or area can only be produced
// from an explicit calibration, and a calibration only becomes "verified"
// after at least one independent check passes.

import { convert, normalizeUnit } from './units.mjs';

export const MODULE_VERSION = '0.2.0';

/** PDF user-space points per inch. */
export const POINTS_PER_INCH = 72;

/** Default tolerance for an independent calibration check, in percent. */
export const DEFAULT_CHECK_TOLERANCE_PCT = 1;

/** Decimal places used for quantities this module returns for storage. */
export const STORED_DECIMALS = 4;

/** A calibration baseline shorter than this (1 in on paper) is flagged. */
export const SHORT_BASELINE_PTS = 72;

export const MEASUREMENT_KINDS = Object.freeze(['line', 'polyline', 'polygon', 'count']);
export const MEASUREMENT_LAYERS = Object.freeze(['existing', 'demolish', 'new', 'field_verify']);
export const MEASUREMENT_SOURCES = Object.freeze(['manual', 'vector_snap', 'ai_suggested']);
export const REVIEW_STATES = Object.freeze(['unreviewed', 'accepted', 'rejected']);
export const CALIBRATION_STATUSES = Object.freeze(['unverified', 'verified', 'failed']);

// Minimum point count per measurement kind.
const MIN_POINTS = Object.freeze({ line: 2, polyline: 2, polygon: 3, count: 1 });

// Geometric tolerance for "same point" / collinearity tests, in PDF points.
const EPS = 1e-9;

// Two check endpoints within this distance of the calibration endpoints mean
// the check re-measured the calibration segment itself (not independent).
const SAME_SEGMENT_TOLERANCE_PTS = 0.5;

/** Error thrown for invalid geometry input; `code` is a stable identifier. */
export class GeometryError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'GeometryError';
    this.code = code;
  }
}

// ---------------------------------------------------------------------------
// Small internal helpers
// ---------------------------------------------------------------------------

function fail(code, message) {
  throw new GeometryError(code, message);
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function roundStored(value) {
  if (value === null) return null;
  const rounded = Number(value.toFixed(STORED_DECIMALS));
  return Object.is(rounded, -0) ? 0 : rounded;
}

function assertPoint(point, label = 'point') {
  if (!isPlainObject(point)) fail('BAD_POINT', `${label} must be an object {x, y}`);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    fail('BAD_POINT', `${label} must have finite numeric x and y`);
  }
}

function assertPoints(points, minCount, label = 'points') {
  if (!Array.isArray(points)) fail('BAD_POINTS', `${label} must be an array of {x, y}`);
  if (points.length < minCount) {
    fail('TOO_FEW_POINTS', `${label} needs at least ${minCount} point(s), got ${points.length}`);
  }
  points.forEach((p, i) => assertPoint(p, `${label}[${i}]`));
}

function assertNonEmptyString(value, label) {
  if (typeof value !== 'string' || value.trim() === '') {
    fail('BAD_FIELD', `${label} must be a non-empty string`);
  }
}

function assertPositiveFinite(value, label) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    fail('BAD_LENGTH', `${label} must be a finite number greater than 0`);
  }
}

function copyPoint(p) {
  return { x: p.x, y: p.y };
}

function samePoint(a, b, tol = EPS) {
  return Math.abs(a.x - b.x) <= tol && Math.abs(a.y - b.y) <= tol;
}

/** Convert a length in any length unit (e.g. 'ft', 'in', 'LF', 'm') to feet. */
function lengthToFeet(value, unitText, label) {
  assertPositiveFinite(value, label);
  if (typeof unitText !== 'string' || unitText.trim() === '') {
    fail('BAD_UNIT', `${label} unit is required (e.g. 'ft' or 'in')`);
  }
  // normalizeUnit throws on unknown text; convert throws UnitError when the
  // unit is not a length (e.g. 'sf'), so a dimension mismatch never slips by.
  return convert(value, normalizeUnit(unitText), 'ft');
}

function assertCalibration(cal, label = 'calibration') {
  if (!isPlainObject(cal)) fail('CALIBRATION_REQUIRED', `${label} is required`);
  if (typeof cal.ft_per_pt !== 'number' || !Number.isFinite(cal.ft_per_pt) || cal.ft_per_pt <= 0) {
    fail('BAD_CALIBRATION', `${label}.ft_per_pt must be a finite number greater than 0`);
  }
}

// ---------------------------------------------------------------------------
// Basic geometry on PDF points
// ---------------------------------------------------------------------------

/** Euclidean distance between two points, in PDF points. */
export function distancePts(a, b) {
  assertPoint(a, 'a');
  assertPoint(b, 'b');
  return Math.hypot(b.x - a.x, b.y - a.y);
}

/** Total length of an open polyline (sum of its segments), in PDF points. */
export function polylineLengthPts(points) {
  assertPoints(points, 2);
  let total = 0;
  for (let i = 1; i < points.length; i += 1) {
    total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
  }
  return total;
}

/** Absolute polygon area by the shoelace formula, in square PDF points. */
export function polygonAreaPts(points) {
  assertPoints(points, 3);
  let twiceArea = 0;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    twiceArea += a.x * b.y - b.x * a.y;
  }
  return Math.abs(twiceArea) / 2;
}

/** Closed perimeter of a polygon (last vertex joins the first), in PDF points. */
export function polygonPerimeterPts(points) {
  assertPoints(points, 3);
  let total = 0;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    total += Math.hypot(b.x - a.x, b.y - a.y);
  }
  return total;
}

// Orientation of the ordered triple (p, q, r): >0 counter-clockwise,
// <0 clockwise, 0 collinear (within EPS scaled to the segment size).
function orientation(p, q, r) {
  const cross = (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  const scale = Math.max(1, Math.abs(q.x - p.x) + Math.abs(q.y - p.y) + Math.abs(r.x - p.x) + Math.abs(r.y - p.y));
  if (Math.abs(cross) <= EPS * scale * scale) return 0;
  return cross > 0 ? 1 : -1;
}

// True when r lies on segment pq, given that p, q, r are collinear.
function onSegment(p, q, r) {
  return (
    r.x <= Math.max(p.x, q.x) + EPS && r.x >= Math.min(p.x, q.x) - EPS &&
    r.y <= Math.max(p.y, q.y) + EPS && r.y >= Math.min(p.y, q.y) - EPS
  );
}

// Closed-segment intersection test (touching endpoints count).
function segmentsIntersect(p1, p2, q1, q2) {
  const o1 = orientation(p1, p2, q1);
  const o2 = orientation(p1, p2, q2);
  const o3 = orientation(q1, q2, p1);
  const o4 = orientation(q1, q2, p2);
  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && onSegment(p1, p2, q1)) return true;
  if (o2 === 0 && onSegment(p1, p2, q2)) return true;
  if (o3 === 0 && onSegment(q1, q2, p1)) return true;
  if (o4 === 0 && onSegment(q1, q2, p2)) return true;
  return false;
}

// Drop consecutive duplicate vertices and an explicit closing vertex.
function cleanRing(points, closed) {
  const ring = [];
  for (const p of points) {
    if (ring.length === 0 || !samePoint(ring[ring.length - 1], p)) ring.push(p);
  }
  if (closed && ring.length > 1 && samePoint(ring[0], ring[ring.length - 1])) ring.pop();
  return ring;
}

/**
 * True when the polygon (closed by default) is not simple: two non-adjacent
 * edges touch or cross, or two adjacent edges fold back over each other.
 * Pass `{closed:false}` to test an open polyline instead.
 */
export function isSelfIntersecting(points, { closed = true } = {}) {
  assertPoints(points, closed ? 3 : 2);
  const ring = cleanRing(points, closed);
  const n = ring.length;
  if (closed && n < 3) return true; // collapsed to a line or a point
  if (!closed && n < 3) return false;

  const edgeCount = closed ? n : n - 1;
  const edge = (i) => [ring[i], ring[(i + 1) % n]];
  const adjacent = (i, j) => {
    if (Math.abs(i - j) === 1) return true;
    return closed && Math.abs(i - j) === edgeCount - 1;
  };

  for (let i = 0; i < edgeCount; i += 1) {
    for (let j = i + 1; j < edgeCount; j += 1) {
      const [a1, a2] = edge(i);
      const [b1, b2] = edge(j);
      if (adjacent(i, j)) {
        // Adjacent edges share one vertex; they only "intersect" if they
        // overlap collinearly beyond it (a spike that doubles back).
        const shared = samePoint(a2, b1) ? a2 : a1;
        const otherA = shared === a2 ? a1 : a2;
        const otherB = samePoint(b1, shared) ? b2 : b1;
        if (orientation(otherA, shared, otherB) === 0) {
          const ax = otherA.x - shared.x;
          const ay = otherA.y - shared.y;
          const bx = otherB.x - shared.x;
          const by = otherB.y - shared.y;
          if (ax * bx + ay * by > 0) return true; // same direction => overlap
        }
        continue;
      }
      if (segmentsIntersect(a1, a2, b1, b2)) return true;
    }
  }
  return false;
}

/**
 * Nearest vertex to `point` within `maxDistPts`, for snapping.
 * Returns `{vertex, index, distance_pts}` or `null` when nothing is close enough.
 * Ties resolve to the lowest index.
 */
export function nearestVertex(point, vertices, maxDistPts) {
  assertPoint(point, 'point');
  if (!Array.isArray(vertices)) fail('BAD_POINTS', 'vertices must be an array of {x, y}');
  if (typeof maxDistPts !== 'number' || !Number.isFinite(maxDistPts) || maxDistPts < 0) {
    fail('BAD_LENGTH', 'maxDistPts must be a finite number >= 0');
  }
  let best = null;
  vertices.forEach((v, index) => {
    assertPoint(v, `vertices[${index}]`);
    const d = Math.hypot(v.x - point.x, v.y - point.y);
    if (d <= maxDistPts && (best === null || d < best.distance_pts)) {
      best = { vertex: copyPoint(v), index, distance_pts: d };
    }
  });
  return best;
}

// ---------------------------------------------------------------------------
// Calibration
// ---------------------------------------------------------------------------

/**
 * Two-point calibration: the user picks two points a known real distance
 * apart. Returns `{sheet_id, ft_per_pt, method:'two_point', p1, p2,
 * known_length_ft, verified:false, warnings, created_at}`. Throws when the
 * points coincide, the length is not > 0, or the unit is not a length unit.
 */
export function calibrationFromPoints({ id = null, sheet_id, p1, p2, known_length, known_unit, created_at } = {}) {
  assertNonEmptyString(sheet_id, 'sheet_id');
  assertPoint(p1, 'p1');
  assertPoint(p2, 'p2');
  const knownLengthFt = lengthToFeet(known_length, known_unit, 'known_length');
  const distance = distancePts(p1, p2);
  if (distance <= EPS) fail('POINTS_COINCIDE', 'calibration points coincide; pick two distinct points');

  const warnings = [];
  if (distance < SHORT_BASELINE_PTS) {
    warnings.push({
      code: 'SHORT_BASELINE',
      message: `calibration baseline is ${distance.toFixed(1)} pt (< 1 in on paper); pick points farther apart to reduce error`,
    });
  }

  return {
    id,
    sheet_id,
    ft_per_pt: knownLengthFt / distance,
    method: 'two_point',
    p1: copyPoint(p1),
    p2: copyPoint(p2),
    known_length,
    known_unit,
    known_length_ft: knownLengthFt,
    baseline_pts: distance,
    verified: false,
    warnings,
    created_at: created_at ?? new Date().toISOString(),
  };
}

/**
 * Calibration from a printed drawing scale, e.g. 1/4" = 1'-0" is
 * `{paper_in: 0.25, real_ft: 1}` → ft_per_pt = 1 / (0.25 × 72) = 1/18.
 * Always `verified:false`: the printed note may be wrong or the PDF may have
 * been resized, so an independent check is still required.
 */
export function calibrationFromPrintedScale({ id = null, sheet_id, paper_in, real_ft, created_at } = {}) {
  assertNonEmptyString(sheet_id, 'sheet_id');
  assertPositiveFinite(paper_in, 'paper_in');
  assertPositiveFinite(real_ft, 'real_ft');
  return {
    id,
    sheet_id,
    ft_per_pt: real_ft / (paper_in * POINTS_PER_INCH),
    method: 'printed_scale',
    paper_in,
    real_ft,
    verified: false,
    warnings: [
      {
        code: 'PRINTED_SCALE_UNCHECKED',
        message: 'printed scale assumes the PDF is at 100% of the original sheet size; confirm with an independent dimension check',
      },
    ],
    created_at: created_at ?? new Date().toISOString(),
  };
}

/**
 * Independent check of a calibration against another known dimension.
 * `known` is `{p1, p2, known_length_ft}` (or `known_length` + `known_unit`).
 * Returns `{measured_ft, error_pct, pass, independent, ...}`. A check that
 * re-measures the calibration's own two points is marked `independent:false`
 * and never verifies the calibration.
 */
export function checkCalibration(cal, known, tolerancePct = DEFAULT_CHECK_TOLERANCE_PCT) {
  assertCalibration(cal);
  if (!isPlainObject(known)) fail('BAD_CHECK', 'check must be an object {p1, p2, known_length_ft}');
  assertPoint(known.p1, 'check.p1');
  assertPoint(known.p2, 'check.p2');
  if (typeof tolerancePct !== 'number' || !Number.isFinite(tolerancePct) || tolerancePct <= 0) {
    fail('BAD_TOLERANCE', 'tolerancePct must be a finite number greater than 0');
  }

  let knownLengthFt = known.known_length_ft;
  if (knownLengthFt === undefined || knownLengthFt === null) {
    knownLengthFt = lengthToFeet(known.known_length, known.known_unit, 'check.known_length');
  } else {
    assertPositiveFinite(knownLengthFt, 'check.known_length_ft');
  }

  const distance = distancePts(known.p1, known.p2);
  if (distance <= EPS) fail('POINTS_COINCIDE', 'check points coincide; pick two distinct points');

  const measuredFt = distance * cal.ft_per_pt;
  const errorPct = (Math.abs(measuredFt - knownLengthFt) / knownLengthFt) * 100;

  // A check repeating the calibration segment (either direction) proves nothing.
  let independent = true;
  if (cal.method === 'two_point' && cal.p1 && cal.p2) {
    const tol = SAME_SEGMENT_TOLERANCE_PTS;
    const sameForward = samePoint(known.p1, cal.p1, tol) && samePoint(known.p2, cal.p2, tol);
    const sameReverse = samePoint(known.p1, cal.p2, tol) && samePoint(known.p2, cal.p1, tol);
    independent = !(sameForward || sameReverse);
  }

  return {
    sheet_id: known.sheet_id ?? cal.sheet_id ?? null,
    p1: copyPoint(known.p1),
    p2: copyPoint(known.p2),
    known_length_ft: knownLengthFt,
    measured_ft: roundStored(measuredFt),
    error_pct: roundStored(errorPct),
    tolerance_pct: tolerancePct,
    // Compare on unrounded values; tiny epsilon absorbs float noise at the boundary.
    pass: errorPct <= tolerancePct + 1e-9,
    independent,
    ft_per_pt_checked: cal.ft_per_pt,
  };
}

/**
 * Calibration status from stored checks: `'failed'` if any independent check
 * fails against the CURRENT calibration, `'verified'` if at least one
 * independent check passes, otherwise `'unverified'`. Checks are re-evaluated
 * (not trusted from a stored `pass` flag) so a changed calibration is re-judged.
 * Checks recorded for a different sheet are ignored.
 */
export function calibrationStatus(cal, checks = []) {
  if (cal === null || cal === undefined) return 'unverified';
  assertCalibration(cal);
  if (!Array.isArray(checks)) fail('BAD_CHECK', 'checks must be an array');

  let passed = 0;
  for (const check of checks) {
    if (!isPlainObject(check)) fail('BAD_CHECK', 'each check must be an object');
    if (check.sheet_id && cal.sheet_id && check.sheet_id !== cal.sheet_id) continue;
    const result = checkCalibration(cal, check, check.tolerance_pct ?? DEFAULT_CHECK_TOLERANCE_PCT);
    if (!result.independent) continue;
    if (!result.pass) return 'failed';
    passed += 1;
  }
  return passed > 0 ? 'verified' : 'unverified';
}

// ---------------------------------------------------------------------------
// Measurement records
// ---------------------------------------------------------------------------

/**
 * Validate a measurement record's shape; throws GeometryError on any problem
 * and returns the record unchanged when valid. Required: id, sheet_id, kind,
 * points (line = 2, polyline >= 2, polygon >= 3, count >= 1, finite coords).
 * Optional fields (layer, source, review, label, assembly) are checked when present.
 */
export function validateMeasurement(m) {
  if (!isPlainObject(m)) fail('BAD_MEASUREMENT', 'measurement must be an object');
  assertNonEmptyString(m.id, 'measurement.id');
  assertNonEmptyString(m.sheet_id, 'measurement.sheet_id');
  if (!MEASUREMENT_KINDS.includes(m.kind)) {
    fail('BAD_KIND', `measurement.kind must be one of ${MEASUREMENT_KINDS.join(', ')}`);
  }
  assertPoints(m.points, MIN_POINTS[m.kind], 'measurement.points');

  if (m.kind === 'line') {
    if (m.points.length !== 2) fail('BAD_POINTS', `a line needs exactly 2 points, got ${m.points.length}`);
    if (samePoint(m.points[0], m.points[1])) fail('ZERO_LENGTH', 'line endpoints coincide');
  }
  if (m.kind === 'polyline' && polylineLengthPts(m.points) <= EPS) {
    fail('ZERO_LENGTH', 'polyline has zero length');
  }
  if (m.kind === 'polygon') {
    if (cleanRing(m.points, true).length < 3) {
      fail('DEGENERATE_POLYGON', 'polygon needs at least 3 distinct vertices');
    }
    if (polygonAreaPts(m.points) <= EPS) {
      fail('ZERO_AREA', 'polygon encloses zero net area (collinear or self-crossing points)');
    }
  }

  if (m.layer !== undefined && !MEASUREMENT_LAYERS.includes(m.layer)) {
    fail('BAD_LAYER', `measurement.layer must be one of ${MEASUREMENT_LAYERS.join(', ')}`);
  }
  if (m.source !== undefined && !MEASUREMENT_SOURCES.includes(m.source)) {
    fail('BAD_SOURCE', `measurement.source must be one of ${MEASUREMENT_SOURCES.join(', ')}`);
  }
  if (m.review !== undefined && !REVIEW_STATES.includes(m.review)) {
    fail('BAD_REVIEW', `measurement.review must be one of ${REVIEW_STATES.join(', ')}`);
  }
  if (m.label !== undefined && m.label !== null && typeof m.label !== 'string') {
    fail('BAD_FIELD', 'measurement.label must be a string or null');
  }
  if (m.assembly !== undefined && m.assembly !== null) {
    if (!isPlainObject(m.assembly)) fail('BAD_FIELD', 'measurement.assembly must be an object or null');
    assertNonEmptyString(m.assembly.id, 'measurement.assembly.id');
    if (m.assembly.params !== undefined && m.assembly.params !== null && !isPlainObject(m.assembly.params)) {
      fail('BAD_FIELD', 'measurement.assembly.params must be an object');
    }
  }
  for (const key of ['created_at', 'updated_at']) {
    if (m[key] !== undefined && m[key] !== null && typeof m[key] !== 'string') {
      fail('BAD_FIELD', `measurement.${key} must be an ISO string or null`);
    }
  }
  return m;
}

/**
 * Real-world quantities for one measurement:
 * `{length_ft, area_sf, perimeter_ft, count}` with `null` for quantities the
 * kind does not produce. line/polyline → length; polygon → area + perimeter;
 * count → number of points (no calibration needed). Values are rounded to
 * STORED_DECIMALS. Throws when a calibration is required but missing, when
 * the calibration belongs to another sheet, or when a polygon self-intersects.
 */
export function measure(m, cal) {
  validateMeasurement(m);
  const result = { length_ft: null, area_sf: null, perimeter_ft: null, count: null };

  if (m.kind === 'count') {
    result.count = m.points.length;
    return result;
  }

  if (cal === null || cal === undefined) {
    fail('CALIBRATION_REQUIRED', `measurement ${m.id} (${m.kind}) needs a calibration for sheet ${m.sheet_id}`);
  }
  assertCalibration(cal);
  if (cal.sheet_id && cal.sheet_id !== m.sheet_id) {
    fail('CALIBRATION_SHEET_MISMATCH', `calibration is for sheet ${cal.sheet_id}, measurement ${m.id} is on sheet ${m.sheet_id}`);
  }

  const k = cal.ft_per_pt;
  if (m.kind === 'line' || m.kind === 'polyline') {
    result.length_ft = roundStored(polylineLengthPts(m.points) * k);
    return result;
  }

  // polygon
  if (isSelfIntersecting(m.points)) {
    fail('SELF_INTERSECTING', `polygon ${m.id} is not simple (edges cross, touch or overlap); its area would be meaningless, retrace it`);
  }
  result.area_sf = roundStored(polygonAreaPts(m.points) * k * k);
  result.perimeter_ft = roundStored(polygonPerimeterPts(m.points) * k);
  return result;
}
