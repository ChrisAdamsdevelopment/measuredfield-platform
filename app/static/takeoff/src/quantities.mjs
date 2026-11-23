// MeasuredField Remodel Takeoff | legacy quick calculator (deterministic, geometry-only).
//
// This module backs the original single-room quick calculator UI. It is kept
// backward compatible: `calculateTakeoff`, `exportCsv` and `SCOPES` keep their
// v0.1 shapes, and every v0.1 line keeps its id and quantity for the same input.
//
// v0.2.0 fixes real planning defects without changing defaults:
//   - drywallFaces      (1|2, default 1)   wall drywall can cover one or both faces;
//                                          the face count is stated in the line name.
//   - includeCeiling    (default false)    adds ceiling SF and ceiling sheet lines.
//   - doorWidthsFt      (default [])       door widths are deducted from base trim LF.
//   - showerTiledWalls  (2|3, default 3)   two-wall (corner) or three-wall (alcove) shower.
//
// Input hardening (fails closed; valid v0.1 inputs give identical results):
//   - numbers must be real numbers or decimal numeric text; booleans, arrays,
//     objects and hex/"Infinity" text are rejected instead of being coerced;
//   - a shower footprint larger than the room, or tile taller than the wall,
//     is rejected as impossible geometry.
//
// NO structural sizing, permit exemption, code compliance, supplier pricing, or field claims.

/** Version of this module (also stamped on every calculateTakeoff result). */
export const MODULE_VERSION = '0.2.0';

/** Supported quick-calculator work scopes. */
export const SCOPES = Object.freeze([
  { id: 'bath', label: 'Bathroom & shower' },
  { id: 'kitchen', label: 'Kitchen & cabinetry' },
  { id: 'interior', label: 'Interior finishes' },
  { id: 'garage', label: 'Room / garage conversion' },
  { id: 'deck', label: 'Deck surface' },
]);

/** Square feet covered by one 4×8 drywall sheet. */
const SHEET_SF = 32;
const REVIEW_STATUS = 'requires field verification';

const round = (x, n = 2) => Number(x.toFixed(n));

/** Decimal numeric text such as "10", " 2.5 ", "-1", ".5" or "1e2" (no hex, no "Infinity"). */
const NUMERIC_TEXT = /^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*$/;

/** Convert a number or decimal numeric text to a number; anything else becomes NaN (never coerced). */
function toNumber(v) {
  if (typeof v === 'number') return v;
  if (typeof v === 'string' && NUMERIC_TEXT.test(v)) return Number(v);
  return NaN;
}

/** Parse a required finite number inside [min, max] or throw a RangeError naming the field. */
function num(v, label, { min = 0, max = 100000 } = {}) {
  const value = toNumber(v);
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new RangeError(label + ' must be a finite number between ' + min + ' and ' + max);
  }
  return value;
}

/** Parse a value that must be one of a small set of integers (accepts numeric strings). */
function oneOf(v, label, allowed, fallback) {
  if (v === undefined || v === null || v === '') return fallback;
  const value = toNumber(v);
  if (!allowed.includes(value)) {
    throw new RangeError(label + ' must be one of ' + allowed.join(', '));
  }
  return value;
}

/** Parse an optional boolean flag; only clear true/false spellings are accepted. */
function flag(v, label) {
  if (v === undefined || v === null || v === '' || v === false || v === 0 || v === 'false' || v === '0') return false;
  if (v === true || v === 1 || v === 'true' || v === '1' || v === 'on') return true;
  throw new TypeError(label + ' must be true or false');
}

/**
 * Parse door widths: an array of numbers or a comma/space separated string ("2.67, 3").
 * Each width must be a finite number between 0.5 and 20 ft.
 */
function doorWidths(v) {
  if (v === undefined || v === null || v === '') return [];
  let parts;
  if (Array.isArray(v)) parts = v;
  else if (typeof v === 'string') parts = v.split(/[\s,;]+/).filter((s) => s !== '');
  else if (typeof v === 'number') parts = [v];
  else throw new TypeError('Door widths must be a list of widths in feet');
  return parts.map((w, i) => num(w, 'Door width #' + (i + 1), { min: 0.5, max: 20 }));
}

const plural = (n, word) => n + ' ' + word + (n === 1 ? '' : 's');

const quantity = (id, name, qty, unit, formula, notes = '') => ({
  id,
  name,
  qty: round(qty),
  unit,
  formula,
  notes,
  review_status: REVIEW_STATUS,
});

/**
 * Compute a quick single-room planning takeoff from entered dimensions.
 * Input: {scope, lengthFt, widthFt, heightFt, wastePct=10, openingsSf=0,
 *         drywallFaces=1, includeCeiling=false, doorWidthsFt=[], showerTiledWalls=3,
 *         showerLengthFt=5, showerWidthFt=3, showerTileHeightFt=7,
 *         baseCabinetLf, upperCabinetLf, backsplashSf}
 * Throws TypeError/RangeError on unknown scope or invalid input (fails closed).
 */
export function calculateTakeoff(input) {
  if (!input || !SCOPES.some((s) => s.id === input.scope)) throw new TypeError('Unknown work scope');
  const length = num(input.lengthFt, 'Length', { min: 0.1, max: 1000 });
  const width = num(input.widthFt, 'Width', { min: 0.1, max: 1000 });
  const waste = num(input.wastePct ?? 10, 'Waste percent', { max: 50 });
  const multiplier = 1 + waste / 100;
  const area = length * width;
  const perimeter = 2 * (length + width);
  const items = [];
  let height = null;
  let options = null;

  if (input.scope === 'deck') {
    items.push(quantity('deck_area', 'Measured deck surface', area, 'SF', 'length × width',
      'No stairs, edge conditions, structural framing, or rails inferred'));
    items.push(quantity('deck_material', 'Decking takeoff allowance', area * multiplier, 'SF', 'deck area × (1 + waste%)',
      'Order units depend on board width, lengths, layout and manufacturer requirements'));
    items.push(quantity('deck_perimeter', 'Geometric deck perimeter', perimeter, 'LF', '2 × (length + width)',
      'Not a code-triggered rail quantity; rail openings and attachment unknown'));
  } else {
    height = num(input.heightFt, 'Wall height', { min: 0.1, max: 40 });
    const openings = num(input.openingsSf ?? 0, 'Wall openings area', { max: 100000 });
    const faces = oneOf(input.drywallFaces, 'Drywall faces', [1, 2], 1);
    const includeCeiling = flag(input.includeCeiling, 'Include ceiling');
    const doors = doorWidths(input.doorWidthsFt);
    const doorTotal = doors.reduce((sum, w) => sum + w, 0);

    const wallGross = perimeter * height;
    if (openings > wallGross) throw new RangeError('Openings exceed gross wall area');
    if (doorTotal > perimeter) throw new RangeError('Door widths exceed room perimeter');
    const wallNet = wallGross - openings;
    const drywallWallSf = wallNet * faces;
    const faceText = plural(faces, 'face');

    items.push(quantity('floor_area', 'Measured room floor area', area, 'SF', 'length × width',
      'Subtract fixtures or excluded zones only after field verification'));
    items.push(quantity('floor_allowance', 'Flooring area with allowance', area * multiplier, 'SF', 'floor area × (1 + waste%)',
      'Supplier carton rounding not yet applied'));
    items.push(quantity('perimeter', 'Gross wall perimeter (before door deductions)', perimeter, 'LF', '2 × (length + width)',
      'Gross geometric run; see base trim line for door deductions'));
    items.push(quantity('base_trim', doors.length ? 'Base trim run, net of ' + plural(doors.length, 'door opening') : 'Base trim run (no door openings entered)',
      perimeter - doorTotal, 'LF',
      doors.length ? '2 × (length + width) − door widths (' + doors.join(' + ') + ' ft)' : '2 × (length + width) − 0 door widths',
      doors.length ? 'Deducts entered door widths only; casing, cabinets and tub/shower runs not deducted'
        : 'No door widths entered: enter each door width to deduct it from base trim'));
    items.push(quantity('wall_net', 'Net wall surface', wallNet, 'SF', 'perimeter × height − entered wall openings',
      'Assumes simple rectangular room and one wall face'));
    items.push(quantity('drywall_wall_area', 'Drywall wall area — ' + faceText, drywallWallSf, 'SF',
      'net wall SF × ' + faceText,
      faces === 1 ? 'One face (room side) of each wall; set drywall faces to 2 to board both faces'
        : 'Both faces of each wall; confirm the opposite faces are in scope'));
    items.push(quantity('drywall_sheets', '4×8 drywall sheets — walls, ' + faceText, Math.ceil(drywallWallSf * multiplier / SHEET_SF), 'sheets',
      'ceil(net wall SF × ' + faceText + ' × (1 + waste%) ÷ 32)',
      'Illustrative 4×8 cut plan for ' + faceText + '; ceiling ' + (includeCeiling ? 'counted on its own line' : 'not included') +
      '; subtract existing-to-remain and tile-backer areas explicitly'));
    if (includeCeiling) {
      items.push(quantity('ceiling_area', 'Ceiling drywall area', area, 'SF', 'length × width',
        'Flat ceiling equal to the floor footprint; soffits, slopes and openings not included'));
      items.push(quantity('ceiling_drywall_sheets', '4×8 drywall sheets — ceiling', Math.ceil(area * multiplier / SHEET_SF), 'sheets',
        'ceil(ceiling SF × (1 + waste%) ÷ 32)', 'Illustrative 4×8 cut plan; ceiling board type and thickness not specified'));
    }

    let tiledWalls = null;
    if (input.scope === 'bath') {
      const showerL = num(input.showerLengthFt ?? 5, 'Shower length', { min: 0.1, max: 50 });
      const showerW = num(input.showerWidthFt ?? 3, 'Shower width', { min: 0.1, max: 50 });
      const showerH = num(input.showerTileHeightFt ?? 7, 'Shower tile height', { min: 0.1, max: 40 });
      const fits = (showerL <= length && showerW <= width) || (showerL <= width && showerW <= length);
      if (!fits) throw new RangeError('Shower footprint ' + showerL + ' × ' + showerW + ' ft does not fit inside the ' + length + ' × ' + width + ' ft room');
      if (showerH > height) throw new RangeError('Shower tile height exceeds wall height');
      tiledWalls = oneOf(input.showerTiledWalls, 'Shower tiled walls', [2, 3], 3);
      const three = tiledWalls === 3;
      const tileRun = three ? 2 * showerL + showerW : showerL + showerW;
      const wallTile = tileRun * showerH;
      const wallsLabel = three ? 'Three-sided' : 'Two-sided';
      const runFormula = three ? '(2 × shower length + shower width)' : '(shower length + shower width)';
      items.push(quantity('shower_walls', wallsLabel + ' shower tile wall area', wallTile, 'SF', runFormula + ' × tile height',
        three ? 'Three tiled walls: both length walls plus one width wall; adjust for window, niche, bench and actual sides'
          : 'Two tiled walls (corner shower): one length wall plus one width wall; adjust for window, niche and bench'));
      items.push(quantity('shower_tile_order', 'Shower wall tile incl. waste', wallTile * multiplier, 'SF',
        (three ? 'three' : 'two') + '-sided shower wall SF × (1 + waste%)',
        'Manufacturer/installer layout and cuts govern actual ordered material'));
      items.push(quantity('shower_pan', 'Shower floor/pan footprint', showerL * showerW, 'SF', 'shower length × shower width',
        'Not a waterproofing membrane system design'));
    }

    if (input.scope === 'kitchen') {
      items.push(quantity('cabinets_base', 'Base cabinet run', num(input.baseCabinetLf ?? 0, 'Base cabinet LF', { max: 500 }), 'LF',
        'contractor-entered base cabinet length', 'Does not count individual boxes or fillers'));
      items.push(quantity('cabinets_upper', 'Upper cabinet run', num(input.upperCabinetLf ?? 0, 'Upper cabinet LF', { max: 500 }), 'LF',
        'contractor-entered upper cabinet length', 'Layout, door swings and ceiling crown unverified'));
      items.push(quantity('backsplash', 'Backsplash tile allowance', num(input.backsplashSf ?? 0, 'Backsplash SF', { max: 5000 }) * multiplier, 'SF',
        'entered backsplash SF × (1 + waste%)', 'Subtract openings and count mosaics/niches separately'));
    }

    options = {
      drywall_faces: faces,
      include_ceiling: includeCeiling,
      door_widths_ft: doors,
      shower_tiled_walls: tiledWalls,
    };
  }

  return {
    version: MODULE_VERSION,
    scope: input.scope,
    entered_dimensions: { lengthFt: length, widthFt: width, heightFt: height },
    options,
    waste_percent: waste,
    code_compliance: 'NOT_EVALUATED',
    permit_status: 'AHJ_VERIFICATION_REQUIRED',
    precision: 'Planning geometry only; human verified plan scale/field conditions required',
    items,
  };
}

/** Quote one CSV cell, neutralizing spreadsheet formula injection (= + - @ tab CR prefixes). */
function cell(v) {
  let s = String(v ?? '');
  if (/^[\s﻿]*[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replaceAll('"', '""') + '"';
}

/** Export a calculateTakeoff result as CRLF CSV with every cell quoted. */
export function exportCsv(result, projectLabel = '') {
  const header = ['Project', 'Scope', 'Item ID', 'Quantity', 'Unit', 'Item', 'Formula', 'Notes', 'Review status'];
  const rows = result.items.map((i) => [projectLabel, result.scope, i.id, i.qty, i.unit, i.name, i.formula, i.notes, i.review_status]);
  return [header, ...rows].map((row) => row.map(cell).join(',')).join('\r\n') + '\r\n';
}
