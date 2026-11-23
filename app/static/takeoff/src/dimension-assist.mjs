/**
 * dimension-assist.mjs — offers PRINTED dimensions as calibration inputs.
 *
 * It never locates dimension-line endpoints: finding the text "3'-9"" says what
 * the length is, not where the line ends. The contractor still clicks both ends,
 * and the existing geometry engine (calibrationFromPoints / checkCalibration)
 * computes and verifies the scale.
 *
 * Coordinates of dimension texts are DISPLAY-normalised (0..1, page rotation
 * applied, y down), as produced by plan-facts.mjs.
 */
import { measure as measureGeometry } from './geometry.mjs';

export const MODULE_VERSION = '0.3.0';

/** Why a printed dimension is not offered for calibration. */
export const EXCLUSION_REASONS = Object.freeze({
  not_a_length: 'Ceiling height, opening size or elevation mark — not a measurable length on this view.',
  approximate: 'Marked approximate (±) — not precise enough to set a scale.',
  labeled: 'Has extra words next to it (for example SETBACK or EASEMENT) — check it before using.',
  out_of_range: 'Too small or too large to be a reliable calibration length.',
  nts_view: 'This drawing is marked NOT TO SCALE — a scale must never be taken from it.',
});

/** Split decimal feet into whole feet + inches (to 1/16"), for the existing inputs. */
export function feetToFeetInches(feet) {
  if (!Number.isFinite(feet) || feet <= 0) throw new RangeError('feet must be a positive number');
  let whole = Math.floor(feet + 1e-9);
  let inches = Math.round((feet - whole) * 12 * 16) / 16;
  if (inches >= 12) { whole += 1; inches = 0; }
  return { feet: whole, inches };
}

/** Human label such as 3'-9" or 2'-8 1/2". */
export function formatFeetInches(feet) {
  const { feet: f, inches } = feetToFeetInches(feet);
  const whole = Math.floor(inches);
  const frac = inches - whole;
  const fracText = frac ? ' ' + toFraction(frac) : '';
  return `${f}'-${whole}${fracText}"`;
}
function toFraction(x) {
  for (const d of [2, 4, 8, 16]) {
    const n = Math.round(x * d);
    if (Math.abs(n / d - x) < 1e-9) return `${n}/${d}`;
  }
  return String(Math.round(x * 1000) / 1000);
}

function stableId(d) {
  const r = v => Math.round((Number(v) || 0) * 10000);
  return ['dim', d.evidence?.doc_id ?? 'doc', d.evidence?.page_index ?? 0, r(d.x), r(d.y), d.text].join('|');
}

/**
 * Classify one extracted dimension for calibration use.
 * @returns {{usable:boolean, reason:string|null}}
 */
export function classifyCandidate(d, { minFeet = 0.5, maxFeet = 300 } = {}) {
  if (d.kind !== 'dimension') return { usable: false, reason: 'not_a_length' };
  if (d.approximate) return { usable: false, reason: 'approximate' };
  if (d.suffix) return { usable: false, reason: 'labeled' };
  if (!(d.feet >= minFeet && d.feet <= maxFeet)) return { usable: false, reason: 'out_of_range' };
  return { usable: true, reason: null };
}

/**
 * Assign each dimension on a page to the nearest drawing-view heading.
 * A page holding several separately scaled views must never share candidates.
 * @param {object[]} dims  plan-facts dimensions for ONE page
 * @param {{key:string,x:number,y:number}[]} anchors view headings in display coordinates
 * @returns {Map<string, object[]>} view key → dimensions ('*' when there are no anchors)
 */
export function assignDimensionsToViews(dims, anchors) {
  const out = new Map();
  const list = (anchors || []).filter(a => Number.isFinite(a?.x) && Number.isFinite(a?.y));
  if (!list.length) { out.set('*', [...dims]); return out; }
  for (const a of list) out.set(a.key, []);
  for (const d of dims) {
    let best = null, bestD = Infinity;
    for (const a of list) {
      // Plan titles usually sit BELOW their drawing; weight horizontal distance more,
      // because side-by-side views share the same vertical band.
      const dist = Math.hypot((d.x - a.x) * 1.6, d.y - a.y);
      if (dist < bestD) { bestD = dist; best = a; }
    }
    out.get(best.key).push(d);
  }
  return out;
}

/**
 * Candidates for the selected view.
 * @param {object} args
 * @param {object[]} args.dimensions plan-facts dimensions (any pages)
 * @param {string|null} args.docId
 * @param {number} args.pageIndex
 * @param {string|null} args.viewKey
 * @param {{key:string,x:number,y:number}[]} args.anchors display-space headings of every view on the page
 * @param {boolean} args.nts  true when the selected view is marked NOT TO SCALE
 * @returns {{usable:object[], excluded:object[], blocked:string|null}}
 */
export function calibrationCandidates({ dimensions, docId = null, pageIndex, viewKey = null, anchors = [], nts = false, limit = 8 }) {
  if (!Array.isArray(dimensions)) throw new TypeError('dimensions array required');
  if (!Number.isInteger(pageIndex)) throw new TypeError('pageIndex required');
  const onPage = dimensions.filter(d => d?.evidence?.page_index === pageIndex && (docId === null || d.evidence.doc_id === docId));
  if (nts) return { usable: [], excluded: onPage.map(d => ({ ...asCandidate(d), reason: 'nts_view' })), blocked: 'nts_view' };
  const byView = assignDimensionsToViews(onPage, anchors);
  const mine = byView.get(viewKey) || byView.get('*') || (anchors.length ? [] : onPage);
  const usable = [], excluded = [];
  const seen = new Set();
  for (const d of mine) {
    const c = asCandidate(d);
    if (seen.has(c.id)) continue;
    seen.add(c.id);
    const cls = classifyCandidate(d);
    if (cls.usable) usable.push(c); else excluded.push({ ...c, reason: cls.reason });
  }
  // Longer printed lengths calibrate more precisely (click error is a smaller fraction).
  usable.sort((a, b) => b.feet - a.feet || a.text.localeCompare(b.text));
  return { usable: usable.slice(0, limit), excluded, blocked: null };
}

function asCandidate(d) {
  return { id: stableId(d), text: d.text, feet: d.feet, label: formatFeetInches(d.feet), x: d.x, y: d.y, w: d.w || 0,
    evidence: d.evidence, kind: d.kind };
}

/**
 * Candidates for the independent check: every usable candidate except the one used
 * to set the scale (same printed text at the same place is not independent).
 */
export function checkCandidates(usable, usedId) {
  return usable.filter(c => c.id !== usedId);
}

/**
 * Quantity impact of replacing a sheet's calibration. Measurements are never changed
 * here; the caller must show this and get the contractor's confirmation first.
 */
export function recalibrationImpact(measurements, oldCal, newCal) {
  if (!newCal) throw new TypeError('new calibration required');
  const rows = [];
  for (const m of measurements || []) {
    if (m.sheet_id !== newCal.sheet_id || m.kind === 'count') continue;
    let before = null, after = null;
    try { before = oldCal ? measureGeometry(m, oldCal) : null; } catch { before = null; }
    try { after = measureGeometry(m, newCal); } catch { after = null; }
    const pick = r => (r ? (m.kind === 'polygon' ? r.area_sf : r.length_ft) : null);
    const b = pick(before), a = pick(after);
    rows.push({
      measurement_id: m.id, label: m.label || m.kind, unit: m.kind === 'polygon' ? 'SF' : 'LF',
      before: b === null ? null : round4(b), after: a === null ? null : round4(a),
      change_pct: b && a !== null ? round4(((a - b) / b) * 100) : null,
    });
  }
  return { rows, affected: rows.length, max_change_pct: rows.reduce((mx, r) => Math.max(mx, Math.abs(r.change_pct || 0)), 0) };
}
const round4 = v => Math.round(v * 10000) / 10000;
