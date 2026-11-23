// MeasuredField Remodel Takeoff — assemblies.mjs (v0.2, spec section 3.5)
//
// Deterministic, bathroom-first assembly library. An assembly turns measured
// geometry (feet, square feet and counts from geometry.measure) plus explicit,
// unit-typed parameters into BOM lines. Every line carries a human-readable
// formula and the assumptions behind it, so a contractor can audit it.
//
// What this module never does:
//   - choose products, brands or prices (catalog.mjs / estimate.mjs do pricing);
//   - size structural members (quantities come only from engineer callouts);
//   - evaluate code compliance;
//   - read the clock or use randomness (output is a pure function of input).

import { convert, normalizeUnit } from './units.mjs';
import { measure, distancePts, validateMeasurement, calibrationStatus } from './geometry.mjs';
import { resolveSheets, filterMeasurementsForTakeoff, detectDuplicateItems } from './revisions.mjs';

export const MODULE_VERSION = '0.2.0';

/** Price classes a BOM line may carry (a pricing hint, not a price). */
export const PRICE_CLASSES = Object.freeze(['retail', 'supplier_quote', 'allowance', 'labor_only', 'by_owner']);

/** Follow-up needs a BOM line may carry, in canonical display order. */
export const NEEDS = Object.freeze(['field_verify', 'engineer_spec', 'owner_selection']);

/** Measurement layers (see geometry.mjs measurement record). */
export const LAYERS = Object.freeze(['existing', 'demolish', 'new', 'field_verify']);

/** Measurement kinds (see geometry.mjs measurement record). */
export const MEASUREMENT_KINDS = Object.freeze(['line', 'polyline', 'polygon', 'count']);

/** Error raised for invalid assembly input (bad params, wrong kinds, impossible geometry). */
export class AssemblyError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AssemblyError';
  }
}

// ---------------------------------------------------------------------------
// Small numeric and text helpers
// ---------------------------------------------------------------------------

const EPS = 1e-9;
const LINEAR_KINDS = Object.freeze(['line', 'polyline']);
const UNIT_LABEL = Object.freeze({ ft: 'LF', sf: 'SF', cf: 'CF', ea: 'EA', in: 'in' });

/** Round to 4 decimal places (stored-output precision), never returning -0. */
function round4(x) {
  const r = Math.round(x * 1e4) / 1e4;
  return Object.is(r, -0) ? 0 : r;
}

/** Display a number with at most 4 decimals and no trailing zeros. */
function fmt(x) {
  return String(round4(x));
}

/** Ceiling that ignores floating-point noise just above an integer. */
function ceilCount(x) {
  return Math.max(0, Math.ceil(x - EPS));
}

function unitLabel(unit) {
  return UNIT_LABEL[unit] ?? unit;
}

/** Display a length in feet as feet-inches, e.g. 3.1667 -> 3'-2". */
function fmtFtIn(ft) {
  const inches = round4(convert(ft, 'ft', 'in'));
  const feet = Math.floor(inches / 12 + EPS);
  const rem = round4(inches - feet * 12);
  return `${feet}'-${fmt(rem)}"`;
}

/** 32-bit FNV-1a hash as 8 hex chars; used only to keep long slugs unique. */
function shortHash(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

/** Deterministic material-key slug from free text (lowercase a-z, 0-9, _). */
function slugify(text, max = 40) {
  const clean = String(text).trim();
  const slug = clean.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (slug && slug.length <= max) return slug;
  const head = slug.slice(0, max).replace(/_+$/g, '');
  return head ? `${head}_${shortHash(clean)}` : `item_${shortHash(clean)}`;
}

/** Stable JSON (sorted object keys) for comparing params. */
function stableStringify(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(',')}}`;
}

function deepFreeze(obj) {
  if (obj && typeof obj === 'object' && !Object.isFrozen(obj)) {
    Object.freeze(obj);
    for (const v of Object.values(obj)) deepFreeze(v);
  }
  return obj;
}

// ---------------------------------------------------------------------------
// Parameter definitions and resolution (unit-typed)
// ---------------------------------------------------------------------------

/**
 * Build a parameter definition. `unit` is a units.mjs code (or null for
 * unitless values); a value supplied as {value, unit} is converted into it.
 */
function param(type, { unit = null, def = null, required = false, min = null, max = null, gt = null, options = null, description = '' } = {}) {
  return { type, unit, default: def, required, min, max, gt, options, description };
}

const P = {
  waste: (def) => param('number', { def, min: 0, max: 100, description: 'Waste allowance in percent applied to qty (planning default, contractor editable).' }),
  height: (description = 'Surface height applied to line/polyline wall runs.') => param('number', { unit: 'ft', gt: 0, max: 100, description }),
  openings: () => param('number', { unit: 'sf', def: 0, min: 0, description: 'Total opening area to deduct (all faces combined).' }),
  framing: (required = true) => param('enum', { options: ['2x4', '2x6'], required, description: 'Nominal stud size of the stated wall assembly.' }),
  spacing: () => param('number', { unit: 'in', def: 16, gt: 0, max: 48, description: 'Stud spacing on center, inches (16 or 24 typical; confirm against plans).' }),
  text: (required, description) => param('text', { required, description }),
  flag: (def, description) => param('boolean', { def, description }),
  int: (def, min, max, description) => param('integer', { def, min, max, description }),
};

const NUMERIC_TEXT = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

function toFiniteNumber(where, raw) {
  let n;
  if (typeof raw === 'number') n = raw;
  else if (typeof raw === 'string' && NUMERIC_TEXT.test(raw.trim())) n = Number(raw.trim());
  else throw new AssemblyError(`${where} must be a number (got ${JSON.stringify(raw)})`);
  if (!Number.isFinite(n)) throw new AssemblyError(`${where} must be a finite number`);
  return n;
}

function coerceParam(assemblyId, name, spec, raw) {
  const where = `${assemblyId}: param "${name}"`;
  switch (spec.type) {
    case 'number':
    case 'integer': {
      let n;
      if (raw !== null && typeof raw === 'object' && !Array.isArray(raw)) {
        if (!('value' in raw) || !('unit' in raw)) throw new AssemblyError(`${where} object form must be {value, unit}`);
        if (spec.unit === null) throw new AssemblyError(`${where} is unitless and cannot take a unit`);
        // Dimension mismatch (e.g. SF given for a length) throws UnitError from units.mjs.
        n = convert(toFiniteNumber(where, raw.value), normalizeUnit(raw.unit), spec.unit);
      } else {
        n = toFiniteNumber(where, raw);
      }
      if (!Number.isFinite(n)) throw new AssemblyError(`${where} must be a finite number`);
      if (spec.type === 'integer' && !Number.isInteger(n)) throw new AssemblyError(`${where} must be a whole number`);
      if (spec.gt !== null && !(n > spec.gt)) throw new AssemblyError(`${where} must be greater than ${spec.gt}`);
      if (spec.min !== null && n < spec.min) throw new AssemblyError(`${where} must be at least ${spec.min}`);
      if (spec.max !== null && n > spec.max) throw new AssemblyError(`${where} must be at most ${spec.max}`);
      return n;
    }
    case 'boolean':
      if (typeof raw !== 'boolean') throw new AssemblyError(`${where} must be true or false`);
      return raw;
    case 'enum':
      if (!spec.options.includes(raw)) throw new AssemblyError(`${where} must be one of ${spec.options.join(', ')}`);
      return raw;
    case 'text': {
      if (typeof raw !== 'string') throw new AssemblyError(`${where} must be text`);
      const s = raw.trim();
      if (s.length > 500) throw new AssemblyError(`${where} is too long (max 500 characters)`);
      return s;
    }
    default:
      throw new AssemblyError(`${where} has unsupported type ${spec.type}`);
  }
}

function isBlank(v) {
  return v === undefined || v === null || (typeof v === 'string' && v.trim() === '');
}

/**
 * Resolve raw params against an assembly's declarations. Unknown names throw
 * (catches typos); blank required params throw; defaults used are recorded so
 * they can be disclosed in each line's assumptions.
 */
function resolveParams(assembly, raw) {
  const source = raw ?? {};
  if (typeof source !== 'object' || Array.isArray(source)) throw new AssemblyError(`${assembly.id}: params must be an object`);
  const decl = assembly.inputs.params;
  for (const name of Object.keys(source)) {
    if (!Object.hasOwn(decl, name)) throw new AssemblyError(`${assembly.id}: unknown param "${name}"`);
  }
  const values = {};
  const defaultsUsed = [];
  for (const [name, spec] of Object.entries(decl)) {
    const v = source[name];
    if (isBlank(v)) {
      if (spec.required) throw new AssemblyError(`${assembly.id}: param "${name}" is required`);
      values[name] = spec.default;
      if (spec.default !== null) defaultsUsed.push(name);
    } else {
      values[name] = coerceParam(assembly.id, name, spec, v);
    }
  }
  return { values, defaultsUsed };
}

function describeParamValue(spec, name, value) {
  if (name.endsWith('_pct')) return `${name} = ${fmt(value)}%`;
  if (typeof value === 'number' && spec.unit) return `${name} = ${fmt(value)} ${unitLabel(spec.unit)}`;
  return `${name} = ${value}`;
}

// ---------------------------------------------------------------------------
// Measured input (what geometry.measure produced, possibly for a group)
// ---------------------------------------------------------------------------

function nonNegOrNull(assemblyId, name, v) {
  if (v === undefined || v === null) return null;
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) {
    throw new AssemblyError(`${assemblyId}: measured ${name} must be a finite number >= 0`);
  }
  return v;
}

function inferKinds(measured) {
  const kinds = [];
  if (measured.length_ft !== undefined && measured.length_ft !== null) kinds.push('polyline');
  if (measured.area_sf !== undefined && measured.area_sf !== null) kinds.push('polygon');
  if (measured.count !== undefined && measured.count !== null) kinds.push('count');
  return kinds;
}

/**
 * Validate and normalize a measured object:
 * {kinds, length_ft, area_sf, perimeter_ft, count, segments_ft}.
 * length_ft sums line/polyline runs, area_sf/perimeter_ft sum polygons,
 * count sums count markers, segments_ft lists each straight run in feet.
 */
function normalizeMeasured(assembly, measured) {
  const id = assembly.id;
  if (!measured || typeof measured !== 'object') throw new AssemblyError(`${id}: measured quantities are required`);
  const kinds = Array.isArray(measured.kinds) ? [...new Set(measured.kinds)] : inferKinds(measured);
  if (kinds.length === 0) throw new AssemblyError(`${id}: no measured quantities supplied`);
  for (const k of kinds) {
    if (!MEASUREMENT_KINDS.includes(k)) throw new AssemblyError(`${id}: unknown measurement kind "${k}"`);
    if (!assembly.inputs.measurement_kinds.includes(k)) {
      throw new AssemblyError(`${id} does not accept ${k} measurements (accepts ${assembly.inputs.measurement_kinds.join(', ')})`);
    }
  }
  const m = {
    kinds,
    length_ft: nonNegOrNull(id, 'length_ft', measured.length_ft),
    area_sf: nonNegOrNull(id, 'area_sf', measured.area_sf),
    perimeter_ft: nonNegOrNull(id, 'perimeter_ft', measured.perimeter_ft),
    count: nonNegOrNull(id, 'count', measured.count),
    segments_ft: [],
  };
  const hasLinear = kinds.some((k) => LINEAR_KINDS.includes(k));
  if (hasLinear && m.length_ft === null) throw new AssemblyError(`${id}: line/polyline measurement is missing length_ft`);
  if (!hasLinear) m.length_ft = null;
  if (kinds.includes('polygon') && m.area_sf === null) throw new AssemblyError(`${id}: polygon measurement is missing area_sf`);
  if (!kinds.includes('polygon')) { m.area_sf = null; m.perimeter_ft = null; }
  if (kinds.includes('count')) {
    if (m.count === null || !Number.isInteger(m.count) || m.count < 1) throw new AssemblyError(`${id}: count measurement must have a whole count >= 1`);
  } else {
    m.count = null;
  }
  if (hasLinear) {
    const segs = Array.isArray(measured.segments_ft) ? measured.segments_ft : [];
    for (const s of segs) nonNegOrNull(id, 'segments_ft[]', s);
    m.segments_ft = segs.length > 0 ? segs.filter((s) => s > EPS) : [m.length_ft];
  }
  return m;
}

/** Combine per-measurement results into one measured object for a group. */
function combineMeasured(parts) {
  const sumOf = (items, key) => items.reduce((a, p) => a + p[key], 0);
  const kinds = [...new Set(parts.map((p) => p.kind))];
  const linear = parts.filter((p) => LINEAR_KINDS.includes(p.kind));
  const polygons = parts.filter((p) => p.kind === 'polygon');
  const counts = parts.filter((p) => p.kind === 'count');
  const perimeters = polygons.filter((p) => p.perimeter_ft !== null && p.perimeter_ft !== undefined);
  return {
    kinds,
    length_ft: linear.length ? sumOf(linear, 'length_ft') : null,
    area_sf: polygons.length ? sumOf(polygons, 'area_sf') : null,
    // A perimeter is only meaningful when every polygon in the group has one.
    perimeter_ft: polygons.length && perimeters.length === polygons.length ? sumOf(polygons, 'perimeter_ft') : null,
    count: counts.length ? sumOf(counts, 'count') : null,
    segments_ft: linear.flatMap((p) => (p.segments_ft && p.segments_ft.length ? p.segments_ft : [p.length_ft])),
  };
}

function segmentsText(m) {
  return m.segments_ft.length > 1 ? ` (${m.segments_ft.map(fmt).join(' + ')})` : '';
}

// ---------------------------------------------------------------------------
// Line construction
// ---------------------------------------------------------------------------

const ID_PATTERN = /^[^\s:+]+$/;

function normalizeContext(assembly, ctx) {
  const c = ctx ?? {};
  const ids = c.measurement_ids ?? [];
  if (!Array.isArray(ids)) throw new AssemblyError(`${assembly.id}: measurement_ids must be an array`);
  for (const mid of ids) {
    if (typeof mid !== 'string' || !ID_PATTERN.test(mid)) {
      throw new AssemblyError(`${assembly.id}: measurement id ${JSON.stringify(mid)} must be non-empty text without spaces, ':' or '+'`);
    }
  }
  const layer = c.layer ?? 'new';
  if (!LAYERS.includes(layer)) throw new AssemblyError(`${assembly.id}: unknown layer "${layer}"`);
  if (!assembly.layer_rule.allowed_layers.includes(layer)) {
    throw new AssemblyError(`${assembly.id}: layer "${layer}" not allowed (${assembly.layer_rule.note})`);
  }
  return {
    measurement_ids: [...new Set(ids)].sort(),
    layer,
    sheet_ids: [...new Set(c.sheet_ids ?? [])].sort(),
    flags: [...new Set(c.flags ?? [])].sort(),
  };
}

/** Build one BOM line with the deterministic spec line_id and validated fields. */
function makeLine(spec) {
  const {
    assembly_id, measurement_ids, layer, material_key, description, qty, unit,
    waste_pct = 0, formula, assumptions, price_class, needs, sheet_ids, flags, extra,
  } = spec;
  const where = `${assembly_id}/${material_key}`;
  if (typeof material_key !== 'string' || !/^[a-z0-9_]+$/.test(material_key)) throw new AssemblyError(`${assembly_id}: invalid material_key ${JSON.stringify(material_key)}`);
  if (!PRICE_CLASSES.includes(price_class)) throw new AssemblyError(`${where}: invalid price_class "${price_class}"`);
  if (layer === 'demolish' && price_class === 'retail') throw new AssemblyError(`${where}: demolish-layer lines are never priced as retail material`);
  for (const n of needs) if (!NEEDS.includes(n)) throw new AssemblyError(`${where}: unknown need "${n}"`);
  if (typeof qty !== 'number' || !Number.isFinite(qty)) throw new AssemblyError(`${where}: quantity is not a finite number`);
  if (qty < -EPS) throw new AssemblyError(`${where}: computed quantity is negative (${fmt(qty)}); check deductions`);
  if (typeof waste_pct !== 'number' || !Number.isFinite(waste_pct) || waste_pct < 0 || waste_pct > 100) throw new AssemblyError(`${where}: waste_pct must be 0..100`);
  if (typeof formula !== 'string' || formula.trim() === '') throw new AssemblyError(`${where}: every line needs a formula`);
  const unitCode = normalizeUnit(unit);
  const q = Math.max(0, round4(qty));
  const factor = 1 + waste_pct / 100;
  const withWaste = unitCode === 'ea' ? ceilCount(q * factor) : round4(q * factor);
  let fullFormula = formula;
  if (waste_pct > 0) {
    fullFormula += unitCode === 'ea'
      ? `; with ${fmt(waste_pct)}% waste: ceil(${fmt(q)} × ${fmt(factor)}) = ${withWaste} EA`
      : `; with ${fmt(waste_pct)}% waste: ${fmt(q)} × ${fmt(factor)} = ${fmt(withWaste)} ${unitLabel(unitCode)}`;
  }
  return {
    line_id: `${assembly_id}:${measurement_ids.join('+')}:${material_key}`,
    assembly_id,
    measurement_ids: [...measurement_ids],
    layer,
    material_key,
    description,
    qty: q,
    unit: unitCode,
    waste_pct,
    qty_with_waste: withWaste,
    formula: fullFormula,
    assumptions: [...assumptions],
    price_class,
    needs: [...needs],
    sheet_ids: [...sheet_ids],
    flags: [...flags],
    ...(extra ?? {}),
  };
}

/** Per-compute line emitter: adds context, layer-driven needs and default disclosures. */
function createEmitter(assembly, ctx, values, defaultsUsed) {
  const lines = [];
  const decl = assembly.inputs.params;
  const defaultsNote = defaultsUsed.length
    ? `Defaults applied (editable): ${defaultsUsed.map((n) => describeParamValue(decl[n], n, values[n])).join(', ')}.`
    : null;
  const emit = (spec) => {
    const needs = new Set(spec.needs ?? []);
    const assumptions = [...(spec.assumptions ?? [])];
    if (ctx.layer === 'field_verify') {
      needs.add('field_verify');
      assumptions.push('Traced on the field_verify layer: confirm in the field before ordering.');
    }
    if (ctx.flags.includes('needs_revision_review')) {
      assumptions.push('Source sheet is partially superseded (clouded revision): review this quantity against the latest revision.');
    }
    if (ctx.flags.includes('calibration_unverified')) {
      assumptions.push('Sheet scale is not independently verified: confirm with a check dimension before relying on this quantity.');
    }
    if (defaultsNote) assumptions.push(defaultsNote);
    lines.push(makeLine({
      ...spec,
      assembly_id: assembly.id,
      measurement_ids: ctx.measurement_ids,
      layer: ctx.layer,
      sheet_ids: ctx.sheet_ids,
      flags: ctx.flags,
      needs: NEEDS.filter((n) => needs.has(n)).concat([...needs].filter((n) => !NEEDS.includes(n))),
      assumptions,
    }));
  };
  emit.lines = lines;
  return emit;
}

/**
 * Define an assembly. `build(m, v, emit)` receives normalized measured values,
 * resolved params and an emitter; the public `compute(measured, params, ctx)`
 * wraps validation around it. ctx = {measurement_ids, layer, sheet_ids, flags}.
 */
function defineAssembly({ id, label, kinds, layers, layerNote, params, build }) {
  const assembly = {
    id,
    label,
    inputs: { measurement_kinds: kinds, params },
    layer_rule: { allowed_layers: layers, note: layerNote },
    compute(measured, rawParams = {}, ctx = {}) {
      const context = normalizeContext(assembly, ctx);
      const m = normalizeMeasured(assembly, measured);
      const { values, defaultsUsed } = resolveParams(assembly, rawParams);
      const emit = createEmitter(assembly, context, values, defaultsUsed);
      build(m, values, emit, context);
      return emit.lines;
    },
  };
  return assembly;
}

// ---------------------------------------------------------------------------
// Shared surface-area helper
// ---------------------------------------------------------------------------

/**
 * Net surface SF = traced polygon area (× faces) + run LF × height (× faces)
 * − openings. Throws when a line run has no height or deductions exceed gross.
 */
function netSurface(assemblyId, m, v, { faces = 1, openingsSf = v.openings_sf ?? 0 } = {}) {
  const terms = [];
  let gross = 0;
  const facesText = faces === 1 ? '' : ` × ${faces} faces`;
  if (m.area_sf !== null) {
    gross += m.area_sf * faces;
    terms.push(`traced area ${fmt(m.area_sf)} SF${facesText}`);
  }
  if (m.length_ft !== null) {
    if (v.height_ft === null || v.height_ft === undefined) {
      throw new AssemblyError(`${assemblyId}: param "height_ft" is required for line/polyline measurements`);
    }
    gross += m.length_ft * v.height_ft * faces;
    terms.push(`run ${fmt(m.length_ft)} ft${segmentsText(m)} × height ${fmt(v.height_ft)} ft${facesText}`);
  }
  const net = gross - openingsSf;
  if (net < -EPS) {
    throw new AssemblyError(`${assemblyId}: openings (${fmt(openingsSf)} SF) exceed gross surface (${fmt(gross)} SF)`);
  }
  const text = `${terms.join(' + ')} − openings ${fmt(openingsSf)} SF = ${fmt(Math.max(0, net))} SF`;
  return { gross, net: Math.max(0, net), text };
}

const NEW_WORK_LAYERS = Object.freeze(['new', 'field_verify']);
const FINISH_LAYERS = Object.freeze(['new', 'existing', 'field_verify']);
const NEW_WORK_NOTE = 'new work: layers new or field_verify';
const FINISH_NOTE = 'layers new, existing (e.g. refinish existing surface) or field_verify; demolition uses the demolition assembly';

const SURFACE_ASSUMPTION = 'Polygon area is used as traced; wall runs use LF × entered height. Openings are deducted only as entered.';

// ---------------------------------------------------------------------------
// Assembly definitions
// ---------------------------------------------------------------------------

const wallDrywall = defineAssembly({
  id: 'wall_drywall',
  label: 'Wall drywall (gypsum board)',
  kinds: ['line', 'polyline', 'polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    height_ft: P.height('Wall height for line/polyline runs.'),
    faces: P.int(1, 1, 2, 'Number of wall faces receiving board (1 or 2).'),
    openings_sf: P.openings(),
    sheet_sf: param('number', { unit: 'sf', gt: 0, max: 200, description: 'Sheet size in SF (e.g. 32 for 4×8). Sheet count is shown only when given.' }),
    board_type: P.text(false, 'Board type/thickness as specified (free text; no product is chosen).'),
    waste_pct: P.waste(10),
  },
  build(m, v, emit) {
    const s = netSurface('wall_drywall', m, v, { faces: v.faces });
    const assumptions = [SURFACE_ASSUMPTION, `Board on ${v.faces} face(s); ceilings are not included.`];
    let extra = { packages_estimate: null };
    if (v.board_type === null) assumptions.push('Board type/thickness not specified: select the product during pricing.');
    if (v.sheet_sf !== null) {
      const withWaste = round4(s.net * (1 + v.waste_pct / 100));
      const sheets = ceilCount(withWaste / v.sheet_sf);
      extra = {
        packages_estimate: {
          package_label: 'sheet',
          coverage_qty: v.sheet_sf,
          coverage_unit: 'sf',
          packages: sheets,
          formula: `ceil(${fmt(withWaste)} SF ÷ ${fmt(v.sheet_sf)} SF per sheet) = ${sheets} sheets`,
        },
      };
      assumptions.push(`Planning sheet count only: ${sheets} sheets of ${fmt(v.sheet_sf)} SF; layout and cut plan are not modelled. Pricing uses the selected product's package.`);
    }
    emit({
      material_key: v.board_type ? `gypsum_board_${slugify(v.board_type)}` : 'gypsum_board',
      description: `Gypsum board${v.board_type ? ` (${v.board_type})` : ''}, ${v.faces} face(s)`,
      qty: s.net,
      unit: 'sf',
      waste_pct: v.waste_pct,
      formula: s.text,
      assumptions,
      price_class: 'retail',
      extra,
    });
  },
});

const TYPICAL_SPACINGS_IN = Object.freeze([16, 24]);

/** Disclosure added when stud spacing is not one of the typical 16/24 in o.c. values. */
function spacingAssumptions(spacingIn) {
  return TYPICAL_SPACINGS_IN.includes(spacingIn)
    ? []
    : [`Non-typical stud spacing ${fmt(spacingIn)} in o.c. used as entered (16 or 24 in is typical): confirm against the plans.`];
}

/** Studs for straight runs: Σ (ceil(run_in ÷ spacing_in) + 1) per run. */
function studsForRuns(runsFt, spacingIn) {
  const parts = runsFt.map((runFt) => {
    const runIn = convert(runFt, 'ft', 'in');
    const studs = ceilCount(runIn / spacingIn) + 1;
    return { runIn, studs, text: `ceil(${fmt(runIn)} in ÷ ${fmt(spacingIn)} in) + 1 = ${studs}` };
  });
  return { total: parts.reduce((a, p) => a + p.studs, 0), parts };
}

const wallFraming = defineAssembly({
  id: 'wall_framing',
  label: 'Wall framing (studs and plates)',
  kinds: ['line', 'polyline'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    wall_framing: P.framing(true),
    stud_spacing_in: P.spacing(),
    plate_count: P.int(3, 1, 6, 'Plates per run (default 3 = one bottom plate + double top plate).'),
    extra_studs: P.int(0, 0, 500, 'Additional studs for corners, intersections and openings (contractor entered).'),
    height_ft: P.height('Wall height (descriptive only; studs are counted, not lengthed).'),
    waste_pct: P.waste(0),
  },
  build(m, v, emit) {
    const size = v.wall_framing;
    const studs = studsForRuns(m.segments_ft, v.stud_spacing_in);
    const total = studs.total + v.extra_studs;
    const runAssumption = m.segments_ft.length > 1
      ? 'Each straight segment is counted as its own run with an end stud; corner and opening studs are added only through extra_studs.'
      : 'One straight run with an end stud at each end; corner and opening studs are added only through extra_studs.';
    emit({
      material_key: `framing_stud_${size}`,
      description: `${size} studs @ ${fmt(v.stud_spacing_in)} in o.c.${v.height_ft !== null ? ` for ${fmt(v.height_ft)} ft wall` : ''}`,
      qty: total,
      unit: 'ea',
      waste_pct: v.waste_pct,
      formula: `Σ runs [${studs.parts.map((p) => p.text).join('; ')}] = ${studs.total} + extra_studs ${v.extra_studs} = ${total} EA`,
      assumptions: [
        runAssumption,
        'Headers, cripples, blocking and fire blocking are not counted; stud length is not chosen here.',
        ...spacingAssumptions(v.stud_spacing_in),
      ],
      price_class: 'retail',
    });
    emit({
      material_key: `framing_plate_${size}`,
      description: `${size} plates (${v.plate_count} per run)`,
      qty: m.length_ft * v.plate_count,
      unit: 'ft',
      waste_pct: v.waste_pct,
      formula: `${fmt(m.length_ft)} ft${segmentsText(m)} × ${v.plate_count} plates = ${fmt(m.length_ft * v.plate_count)} LF`,
      assumptions: ['Plate material (e.g. treated bottom plate on concrete) is not distinguished: confirm with plans.'],
      price_class: 'retail',
    });
  },
});

const tileWall = defineAssembly({
  id: 'tile_wall',
  label: 'Wall tile',
  kinds: ['line', 'polyline', 'polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    height_ft: P.height('Tile height for line/polyline wall runs.'),
    openings_sf: P.openings(),
    waste_pct: P.waste(10),
  },
  build(m, v, emit) {
    const s = netSurface('tile_wall', m, v);
    emit({
      material_key: 'tile_wall',
      description: 'Wall tile field area',
      qty: s.net,
      unit: 'sf',
      waste_pct: v.waste_pct,
      formula: s.text,
      assumptions: [SURFACE_ASSUMPTION, 'Niches, benches, trim pieces and layout-driven cuts are not included.'],
      price_class: 'retail',
      needs: ['owner_selection'],
    });
  },
});

const tileFloor = defineAssembly({
  id: 'tile_floor',
  label: 'Floor tile',
  kinds: ['polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    excluded_sf: param('number', { unit: 'sf', def: 0, min: 0, description: 'Area to exclude (fixtures, cabinets) as entered.' }),
    waste_pct: P.waste(10),
  },
  build(m, v, emit) {
    const net = m.area_sf - v.excluded_sf;
    if (net < -EPS) throw new AssemblyError(`tile_floor: excluded_sf (${fmt(v.excluded_sf)} SF) exceeds traced area (${fmt(m.area_sf)} SF)`);
    emit({
      material_key: 'tile_floor',
      description: 'Floor tile field area',
      qty: Math.max(0, net),
      unit: 'sf',
      waste_pct: v.waste_pct,
      formula: `traced area ${fmt(m.area_sf)} SF − excluded ${fmt(v.excluded_sf)} SF = ${fmt(Math.max(0, net))} SF`,
      assumptions: ['Floor area as traced; excluded zones are deducted only as entered.'],
      price_class: 'retail',
      needs: ['owner_selection'],
    });
  },
});

const cementBoard = defineAssembly({
  id: 'cement_board',
  label: 'Cement backer board',
  kinds: ['line', 'polyline', 'polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    height_ft: P.height('Board height for line/polyline wall runs.'),
    openings_sf: P.openings(),
    waste_pct: P.waste(10),
  },
  build(m, v, emit) {
    const s = netSurface('cement_board', m, v);
    emit({
      material_key: 'cement_backer_board',
      description: 'Cement backer board',
      qty: s.net,
      unit: 'sf',
      waste_pct: v.waste_pct,
      formula: s.text,
      assumptions: [SURFACE_ASSUMPTION, 'Fasteners, tape and sealant follow the selected system and are not counted here.'],
      price_class: 'retail',
    });
  },
});

const waterproofingMembrane = defineAssembly({
  id: 'waterproofing_membrane',
  label: 'Waterproofing membrane',
  kinds: ['line', 'polyline', 'polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    height_ft: P.height('Membrane height for line/polyline wall runs.'),
    upturn_height_in: param('number', { unit: 'in', def: 0, min: 0, max: 120, description: 'Upturn height applied along traced floor polygon perimeters.' }),
    curb_sf: param('number', { unit: 'sf', def: 0, min: 0, description: 'Additional curb surface area, as entered (0 for curbless).' }),
    openings_sf: P.openings(),
    waste_pct: P.waste(10),
  },
  build(m, v, emit) {
    const terms = [];
    let gross = 0;
    if (m.area_sf !== null) {
      gross += m.area_sf;
      terms.push(`floor ${fmt(m.area_sf)} SF`);
      const upturnFt = convert(v.upturn_height_in, 'in', 'ft');
      const perim = m.perimeter_ft ?? 0;
      if (v.upturn_height_in > 0 && m.perimeter_ft === null) {
        throw new AssemblyError('waterproofing_membrane: upturn needs the traced polygon perimeter');
      }
      gross += perim * upturnFt;
      terms.push(`upturn ${fmt(perim)} ft × ${fmt(v.upturn_height_in)} in (${fmt(upturnFt)} ft)`);
    }
    if (m.length_ft !== null) {
      if (v.height_ft === null) throw new AssemblyError('waterproofing_membrane: param "height_ft" is required for line/polyline measurements');
      gross += m.length_ft * v.height_ft;
      terms.push(`walls ${fmt(m.length_ft)} ft${segmentsText(m)} × ${fmt(v.height_ft)} ft`);
    }
    gross += v.curb_sf;
    terms.push(`curb ${fmt(v.curb_sf)} SF`);
    const net = gross - v.openings_sf;
    if (net < -EPS) throw new AssemblyError(`waterproofing_membrane: openings (${fmt(v.openings_sf)} SF) exceed gross membrane area (${fmt(gross)} SF)`);
    emit({
      material_key: 'waterproofing_membrane',
      description: 'Waterproofing membrane coverage',
      qty: Math.max(0, net),
      unit: 'sf',
      waste_pct: v.waste_pct,
      formula: `${terms.join(' + ')} − openings ${fmt(v.openings_sf)} SF = ${fmt(Math.max(0, net))} SF`,
      assumptions: [
        'Coverage area only; seams, corners, drain flanges and penetrations follow the selected manufacturer system.',
        'This is not a waterproofing design and does not evaluate code or manufacturer compliance.',
      ],
      price_class: 'retail',
    });
  },
});

const showerPanMortarBed = defineAssembly({
  id: 'shower_pan_mortar_bed',
  label: 'Shower pan mortar bed',
  kinds: ['polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    depth_in: param('number', { unit: 'in', gt: 0, max: 12, description: 'Average mortar bed thickness in inches; volume is computed only when given.' }),
    waste_pct: P.waste(0),
  },
  build(m, v, emit) {
    const assumptions = ['Slope to drain and bed design follow the plans; this is a quantity only.'];
    if (v.depth_in === null) assumptions.push('Mortar volume not computed: depth_in not provided.');
    emit({
      material_key: 'mortar_bed_area',
      description: 'Shower pan mortar bed (installed area)',
      qty: m.area_sf,
      unit: 'sf',
      waste_pct: 0,
      formula: `traced pan area ${fmt(m.area_sf)} SF`,
      assumptions,
      price_class: 'labor_only',
    });
    if (v.depth_in !== null) {
      const depthFt = convert(v.depth_in, 'in', 'ft');
      const cf = m.area_sf * depthFt;
      emit({
        material_key: 'mortar_bed_mix',
        description: `Mortar bed mix, ${fmt(v.depth_in)} in average depth`,
        qty: cf,
        unit: 'cf',
        waste_pct: v.waste_pct,
        formula: `${fmt(m.area_sf)} SF × ${fmt(v.depth_in)} in (${fmt(depthFt)} ft) = ${fmt(cf)} CF`,
        assumptions: ['Average depth as entered; bag yield comes from the selected product package.'],
        price_class: 'retail',
      });
    }
  },
});

const linearDrain = defineAssembly({
  id: 'linear_drain',
  label: 'Linear drain',
  kinds: ['count', 'line', 'polyline'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {},
  build(m, v, emit) {
    if (m.count !== null && m.length_ft !== null) throw new AssemblyError('linear_drain: group mixes count and length measurements');
    const byCount = m.count !== null;
    emit({
      material_key: 'linear_drain',
      description: byCount ? 'Linear drain assembly' : 'Linear drain channel length',
      qty: byCount ? m.count : m.length_ft,
      unit: byCount ? 'ea' : 'ft',
      waste_pct: 0,
      formula: byCount ? `counted ${m.count} EA` : `traced ${fmt(m.length_ft)} LF`,
      assumptions: ['Drain body, grate and outlet location are owner/plumber selections; rough-in location must be field verified.'],
      price_class: 'retail',
      needs: ['field_verify', 'owner_selection'],
    });
  },
});

const insulationBatt = defineAssembly({
  id: 'insulation_batt',
  label: 'Batt insulation',
  kinds: ['line', 'polyline', 'polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    height_ft: P.height('Insulated height for line/polyline wall runs.'),
    openings_sf: P.openings(),
    r_value: param('number', { gt: 0, max: 100, description: 'R-value as specified on plans (never assumed).' }),
    waste_pct: P.waste(10),
  },
  build(m, v, emit) {
    const s = netSurface('insulation_batt', m, v);
    const assumptions = [SURFACE_ASSUMPTION];
    const needs = [];
    if (v.r_value === null) {
      assumptions.push('R-value not specified: confirm the required R-value from plans/AHJ before pricing.');
      needs.push('field_verify');
    }
    emit({
      material_key: v.r_value === null ? 'insulation_batt' : `insulation_batt_r${slugify(fmt(v.r_value))}`,
      description: `Batt insulation${v.r_value === null ? ' (R-value not specified)' : `, R-${fmt(v.r_value)} as specified`}`,
      qty: s.net,
      unit: 'sf',
      waste_pct: v.waste_pct,
      formula: s.text,
      assumptions,
      price_class: 'retail',
      needs,
    });
  },
});

const WINDOW_LAYERS = Object.freeze([
  { flag: 'include_insulation', key: 'insulation', label: 'cavity insulation' },
  { flag: 'include_exterior_sheathing', key: 'sheathing', label: 'exterior sheathing' },
  { flag: 'include_wrb', key: 'wrb', label: 'weather-resistive barrier (WRB)' },
  { flag: 'include_foam', key: 'foam', label: 'exterior foam board' },
  { flag: 'include_stucco', key: 'stucco', label: 'stucco' },
  { flag: 'include_interior_drywall', key: 'drywall', label: 'interior drywall' },
]);

const windowInfill = defineAssembly({
  id: 'window_infill',
  label: 'Window opening infill',
  kinds: ['count'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    width_ft: param('number', { unit: 'ft', required: true, gt: 0, max: 40, description: 'Opening width (e.g. {value:38, unit:"in"} for 3\'-2").' }),
    height_ft: param('number', { unit: 'ft', required: true, gt: 0, max: 40, description: 'Opening height.' }),
    wall_framing: P.framing(true),
    stud_spacing_in: P.spacing(),
    plate_count: P.int(2, 0, 4, 'Plates across the opening (default 2: new bottom plate + top plate under existing header).'),
    include_interior_drywall: P.flag(true, 'Include interior drywall patch.'),
    include_exterior_sheathing: P.flag(true, 'Include exterior sheathing patch.'),
    include_foam: P.flag(true, 'Include exterior foam board patch.'),
    include_stucco: P.flag(true, 'Include stucco patch.'),
    include_insulation: P.flag(true, 'Include cavity insulation.'),
    include_wrb: P.flag(true, 'Include weather-resistive barrier patch.'),
    insulation_r_value: param('number', { gt: 0, max: 100, description: 'Cavity insulation R-value as specified (never assumed).' }),
    waste_pct: P.waste(0),
  },
  build(m, v, emit) {
    const n = m.count;
    const size = v.wall_framing;
    const dims = `${fmtFtIn(v.width_ft)} × ${fmtFtIn(v.height_ft)}`;
    const openingsText = `${n} opening${n === 1 ? '' : 's'}`;
    const included = WINDOW_LAYERS.filter((l) => v[l.flag]).map((l) => l.label);
    const assemblyText = `${size} @ ${fmt(v.stud_spacing_in)} in o.c. framing${included.length ? ` with ${included.join(', ')}` : ''}`;
    const derived = `Derived from the stated wall assembly (${assemblyText}); actual existing wall layers and thicknesses must be field verified before ordering.`;
    const framingNote = 'Assumes the existing header, king and jack studs remain; infill studs are cut to fit between the new plates.';

    const widthIn = convert(v.width_ft, 'ft', 'in');
    const perOpening = ceilCount(widthIn / v.stud_spacing_in) + 1;
    emit({
      material_key: `framing_stud_${size}`,
      description: `${size} infill studs for ${dims} opening (cut to fit)`,
      qty: perOpening * n,
      unit: 'ea',
      waste_pct: v.waste_pct,
      formula: `per opening: ceil(${fmt(widthIn)} in ÷ ${fmt(v.stud_spacing_in)} in) + 1 = ceil(${fmt(widthIn / v.stud_spacing_in)}) + 1 = ${perOpening} studs; × ${openingsText} = ${perOpening * n} EA`,
      assumptions: [derived, framingNote, ...spacingAssumptions(v.stud_spacing_in)],
      price_class: 'retail',
      needs: ['field_verify'],
    });
    if (v.plate_count > 0) {
      const plateLf = v.width_ft * v.plate_count * n;
      emit({
        material_key: `framing_plate_${size}`,
        description: `${size} infill plates for ${dims} opening`,
        qty: plateLf,
        unit: 'ft',
        waste_pct: v.waste_pct,
        formula: `${fmt(v.width_ft)} ft × ${v.plate_count} plates × ${openingsText} = ${fmt(plateLf)} LF`,
        assumptions: [derived, framingNote],
        price_class: 'retail',
        needs: ['field_verify'],
      });
    }

    const areaEach = v.width_ft * v.height_ft;
    const area = areaEach * n;
    const areaFormula = `${fmt(v.width_ft)} ft × ${fmt(v.height_ft)} ft × ${openingsText} = ${fmt(area)} SF`;
    const patchNote = 'Patch area equals the opening area only; laps, overlaps, blending into existing finish and trim are not included.';
    const layerLines = {
      insulation: {
        material_key: v.insulation_r_value === null ? 'insulation_batt' : `insulation_batt_r${slugify(fmt(v.insulation_r_value))}`,
        description: `Cavity insulation for ${size} infill${v.insulation_r_value === null ? ' (R-value not specified)' : `, R-${fmt(v.insulation_r_value)} as specified`}`,
        extra: v.insulation_r_value === null ? ['R-value not specified: confirm required R-value from plans/AHJ.'] : [],
      },
      sheathing: { material_key: 'exterior_sheathing', description: 'Exterior sheathing patch', extra: [patchNote] },
      wrb: { material_key: 'weather_resistive_barrier', description: 'Weather-resistive barrier patch', extra: [patchNote, 'Lapping and integration with the existing WRB must be detailed in the field.'] },
      foam: { material_key: 'exterior_foam_board', description: 'Exterior foam board patch', extra: [patchNote] },
      stucco: { material_key: 'stucco_patch', description: 'Stucco patch', extra: [patchNote, 'Lath, coat count and finish match follow the existing stucco system.'] },
      drywall: { material_key: 'gypsum_board', description: 'Interior drywall patch', extra: [patchNote] },
    };
    for (const layer of WINDOW_LAYERS) {
      if (!v[layer.flag]) continue;
      const spec = layerLines[layer.key];
      emit({
        material_key: spec.material_key,
        description: `${spec.description} for ${dims} opening`,
        qty: area,
        unit: 'sf',
        waste_pct: v.waste_pct,
        formula: areaFormula,
        assumptions: [
          derived,
          `Included because ${layer.flag} = true; set it to false if the existing wall has no ${layer.label}.`,
          ...spec.extra,
        ],
        price_class: 'retail',
        needs: ['field_verify'],
      });
    }
  },
});

const demolition = defineAssembly({
  id: 'demolition',
  label: 'Demolition and disposal',
  kinds: ['line', 'polyline', 'polygon', 'count'],
  layers: Object.freeze(['demolish']),
  layerNote: 'demolish layer only',
  params: {
    description: P.text(true, 'What is removed (e.g. "existing wall tile and backer").'),
    height_ft: P.height('Height for line/polyline runs to report removal in SF (otherwise LF).'),
  },
  build(m, v, emit) {
    let qty;
    let unit;
    let formula;
    if (m.count !== null && (m.length_ft !== null || m.area_sf !== null)) {
      throw new AssemblyError('demolition: group mixes count and length/area measurements');
    }
    if (m.count !== null) {
      qty = m.count; unit = 'ea'; formula = `counted ${m.count} EA removed`;
    } else if (m.area_sf !== null || v.height_ft !== null) {
      if (m.area_sf !== null && m.length_ft !== null && v.height_ft === null) {
        throw new AssemblyError('demolition: group mixes area and length; give height_ft to report SF');
      }
      const s = netSurface('demolition', m, v, { openingsSf: 0 });
      qty = s.net; unit = 'sf'; formula = s.text.replace(/ − openings 0 SF/, '');
    } else {
      qty = m.length_ft; unit = 'ft'; formula = `traced ${fmt(m.length_ft)} LF${segmentsText(m)} removed`;
    }
    const key = slugify(v.description);
    emit({
      material_key: `demo_${key}`,
      description: `Remove: ${v.description}`,
      qty,
      unit,
      waste_pct: 0,
      formula,
      assumptions: ['Removal quantity as traced on the demolish layer; hidden conditions (rot, substrate, utilities) are not known until opened up.'],
      price_class: 'labor_only',
      needs: ['field_verify'],
    });
    emit({
      material_key: `disposal_${key}`,
      description: `Disposal allowance: ${v.description}`,
      qty,
      unit,
      waste_pct: 0,
      formula: `disposal basis = removed quantity ${fmt(qty)} ${unitLabel(unit)}`,
      assumptions: ['Disposal (haul-off, container, fees) is carried as an allowance; no disposal price is implied.'],
      price_class: 'allowance',
      needs: ['field_verify'],
    });
  },
});

const paint = defineAssembly({
  id: 'paint',
  label: 'Paint',
  kinds: ['line', 'polyline', 'polygon'],
  layers: FINISH_LAYERS,
  layerNote: FINISH_NOTE,
  params: {
    height_ft: P.height('Painted height for line/polyline wall runs.'),
    openings_sf: P.openings(),
    coats: P.int(2, 1, 5, 'Number of coats (coverage area = SF × coats).'),
    waste_pct: P.waste(0),
  },
  build(m, v, emit) {
    const s = netSurface('paint', m, v);
    const coverage = s.net * v.coats;
    emit({
      material_key: 'paint',
      description: `Paint coverage area, ${v.coats} coat(s)`,
      qty: coverage,
      unit: 'sf',
      waste_pct: v.waste_pct,
      formula: `(${s.text}) × ${v.coats} coats = ${fmt(coverage)} SF`,
      assumptions: [SURFACE_ASSUMPTION, 'Quantity is coverage area (SF × coats); gallons follow the selected product coverage. Primer is not included.'],
      price_class: 'retail',
      needs: ['owner_selection'],
    });
  },
});

const baseTrim = defineAssembly({
  id: 'base_trim',
  label: 'Base trim',
  kinds: ['line', 'polyline', 'polygon'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    door_openings_lf: param('number', { unit: 'ft', def: 0, min: 0, description: 'Total width of door openings to deduct.' }),
    waste_pct: P.waste(10),
  },
  build(m, v, emit) {
    const terms = [];
    let gross = 0;
    if (m.length_ft !== null) { gross += m.length_ft; terms.push(`run ${fmt(m.length_ft)} ft${segmentsText(m)}`); }
    if (m.area_sf !== null) {
      if (m.perimeter_ft === null) throw new AssemblyError('base_trim: polygon measurement is missing perimeter_ft');
      gross += m.perimeter_ft;
      terms.push(`room perimeter ${fmt(m.perimeter_ft)} ft`);
    }
    const net = gross - v.door_openings_lf;
    if (net < -EPS) throw new AssemblyError(`base_trim: door openings (${fmt(v.door_openings_lf)} ft) exceed traced length (${fmt(gross)} ft)`);
    emit({
      material_key: 'base_trim',
      description: 'Base trim',
      qty: Math.max(0, net),
      unit: 'ft',
      waste_pct: v.waste_pct,
      formula: `${terms.join(' + ')} − door openings ${fmt(v.door_openings_lf)} ft = ${fmt(Math.max(0, net))} LF`,
      assumptions: ['Door openings are deducted only as entered; cabinets and fixtures along walls are not deducted.'],
      price_class: 'retail',
      needs: ['owner_selection'],
    });
  },
});

const countItem = defineAssembly({
  id: 'count_item',
  label: 'Counted item',
  kinds: ['count'],
  layers: FINISH_LAYERS,
  layerNote: FINISH_NOTE,
  params: {
    description: P.text(true, 'What is counted (free text; no product is chosen).'),
    material_key: P.text(false, 'Catalog material key (default: derived from description).'),
    price_class: param('enum', { options: [...PRICE_CLASSES], def: 'retail', description: 'Pricing class for this item.' }),
    requires_owner_selection: P.flag(false, 'Flag the line as needing an owner selection.'),
  },
  build(m, v, emit) {
    let key = `item_${slugify(v.description)}`;
    if (v.material_key !== null) {
      if (!/^[a-z0-9_]+$/.test(v.material_key)) throw new AssemblyError('count_item: material_key may only contain a-z, 0-9 and _');
      key = v.material_key;
    }
    emit({
      material_key: key,
      description: v.description,
      qty: m.count,
      unit: 'ea',
      waste_pct: 0,
      formula: `counted ${m.count} EA`,
      assumptions: ['Count as marked on the plan; product, size and finish are not chosen here.'],
      price_class: v.price_class,
      needs: v.requires_owner_selection ? ['owner_selection'] : [],
    });
  },
});

const structuralMember = defineAssembly({
  id: 'structural_member',
  label: 'Structural member (from engineer callout)',
  kinds: ['line', 'polyline', 'count'],
  layers: NEW_WORK_LAYERS,
  layerNote: NEW_WORK_NOTE,
  params: {
    callout: P.text(true, 'Engineer callout text exactly as shown on the structural drawings.'),
    callout_ref: P.text(false, 'Where the callout appears (sheet / detail).'),
    member_count: P.int(1, 1, 100, 'Pieces (plies) per location, from the callout.'),
    member_length_ft: param('number', { unit: 'ft', gt: 0, max: 100, description: 'Piece length from the callout (count measurements only).' }),
    price_class: param('enum', { options: [...PRICE_CLASSES], def: 'supplier_quote', description: 'Pricing class (default supplier_quote).' }),
  },
  build(m, v, emit) {
    if (m.count !== null && m.length_ft !== null) throw new AssemblyError('structural_member: group mixes count and length measurements');
    let qty;
    let formula;
    if (m.count !== null) {
      if (v.member_length_ft === null) throw new AssemblyError('structural_member: member_length_ft from the engineer callout is required for count measurements');
      qty = m.count * v.member_count * v.member_length_ft;
      formula = `${m.count} location(s) × ${v.member_count} piece(s) × ${fmt(v.member_length_ft)} ft (from callout) = ${fmt(qty)} LF`;
    } else {
      if (v.member_length_ft !== null) throw new AssemblyError('structural_member: member_length_ft applies to count measurements; a traced line already gives the length');
      qty = m.length_ft * v.member_count;
      formula = `traced ${fmt(m.length_ft)} ft${segmentsText(m)} × ${v.member_count} piece(s) per callout = ${fmt(qty)} LF`;
    }
    emit({
      material_key: `structural_${slugify(v.callout)}`,
      description: `Structural member per engineer callout: "${v.callout}"${v.callout_ref ? ` (${v.callout_ref})` : ''}`,
      qty,
      unit: 'ft',
      waste_pct: 0,
      formula,
      assumptions: [
        'MeasuredField does not size structural members. Quantity comes only from the engineer callout as entered.',
        'Verify member, grade, length, bearing and connections against the stamped structural drawings before ordering.',
      ],
      price_class: v.price_class,
      needs: ['engineer_spec'],
    });
  },
});

const allowance = defineAssembly({
  id: 'allowance',
  label: 'Allowance (lump sum)',
  kinds: ['line', 'polyline', 'polygon', 'count'],
  layers: LAYERS,
  layerNote: 'any layer',
  params: {
    description: P.text(true, 'What the allowance covers.'),
  },
  build(m, v, emit) {
    emit({
      material_key: `allowance_${slugify(v.description)}`,
      description: `Allowance: ${v.description}`,
      qty: 1,
      unit: 'ea',
      waste_pct: 0,
      formula: 'lump-sum allowance: 1 EA (amount is set in pricing)',
      assumptions: ['Allowance placeholder; the trace only locates it. No amount is implied.'],
      price_class: 'allowance',
      needs: [],
    });
  },
});

/** Registry of all assemblies, keyed by id. Frozen. */
export const ASSEMBLIES = deepFreeze({
  wall_drywall: wallDrywall,
  wall_framing: wallFraming,
  tile_wall: tileWall,
  tile_floor: tileFloor,
  cement_board: cementBoard,
  waterproofing_membrane: waterproofingMembrane,
  shower_pan_mortar_bed: showerPanMortarBed,
  linear_drain: linearDrain,
  insulation_batt: insulationBatt,
  window_infill: windowInfill,
  demolition,
  paint,
  base_trim: baseTrim,
  count_item: countItem,
  structural_member: structuralMember,
  allowance,
});

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Return the assembly definition for `id`; throws AssemblyError if unknown. */
export function getAssembly(id) {
  if (typeof id !== 'string' || !Object.hasOwn(ASSEMBLIES, id)) throw new AssemblyError(`unknown assembly "${id}"`);
  return ASSEMBLIES[id];
}

/** List assembly metadata (no functions) for UI pickers and documentation. */
export function listAssemblies() {
  return Object.values(ASSEMBLIES).map((a) => ({
    id: a.id,
    label: a.label,
    inputs: JSON.parse(JSON.stringify(a.inputs)),
    layer_rule: JSON.parse(JSON.stringify(a.layer_rule)),
  }));
}

/**
 * Compute BOM lines for one assembly from measured values.
 * measured: {kinds?, length_ft?, area_sf?, perimeter_ft?, count?, segments_ft?}
 * ctx: {measurement_ids, layer, sheet_ids?, flags?}. Throws on invalid input.
 */
export function computeAssembly(assemblyId, measured, params = {}, ctx = {}) {
  return getAssembly(assemblyId).compute(measured, params, ctx);
}

const SHEET_STATUSES = new Set(['effective', 'superseded', 'partially_superseded', 'conflict']);

function looksResolved(sheets) {
  return sheets.length > 0 && sheets.every((s) => s && SHEET_STATUSES.has(s.status));
}

/** Accept either a bare measurement or a wrapper {measurement, flags} from revisions. */
function unwrapIncluded(entry) {
  const m = entry && typeof entry === 'object' && entry.measurement && typeof entry.measurement === 'object' ? entry.measurement : entry;
  const flags = new Set();
  for (const src of [entry, m]) {
    if (!src || typeof src !== 'object') continue;
    if (src.needs_revision_review === true) flags.add('needs_revision_review');
    for (const key of ['flags', 'review_flags', 'revision_flags']) {
      if (Array.isArray(src[key]) && src[key].includes('needs_revision_review')) flags.add('needs_revision_review');
    }
  }
  return { m, flags };
}

/**
 * Map sheet_id -> 'verified'|'unverified'|'failed' for each calibration.
 * With checks: geometry.calibrationStatus on the checks for that sheet (a
 * malformed check is reported and leaves the sheet unverified). Without
 * checks: only an explicit `verified === true` is trusted.
 */
function calibrationTrust(calBySheet, checks, warnings) {
  const trust = new Map();
  for (const [sheetId, cal] of calBySheet) {
    if (checks === null) {
      trust.set(sheetId, cal.verified === true ? 'verified' : 'unverified');
      continue;
    }
    const forSheet = checks.filter((c) => c && typeof c === 'object' && c.sheet_id === sheetId);
    let status;
    try {
      status = calibrationStatus(cal, forSheet);
    } catch (err) {
      warnings.push({ code: 'CALIBRATION_CHECK_INVALID', sheet_id: sheetId, message: `Calibration check for sheet ${sheetId} could not be evaluated: ${err.message}` });
      status = 'unverified';
    }
    if (status === 'failed') {
      warnings.push({ code: 'CALIBRATION_FAILED', sheet_id: sheetId, message: `Calibration for sheet ${sheetId} failed an independent check; its length/area measurements are excluded.` });
    }
    trust.set(sheetId, status);
  }
  return trust;
}

function linearSegmentsFt(m, cal) {
  if (!LINEAR_KINDS.includes(m.kind)) return [];
  const segs = [];
  for (let i = 0; i + 1 < m.points.length; i += 1) {
    const ft = distancePts(m.points[i], m.points[i + 1]) * cal.ft_per_pt;
    if (ft > EPS) segs.push(ft);
  }
  return segs;
}

/**
 * Compute the takeoff for a project: applies revisions.filterMeasurementsForTakeoff
 * (superseded sheets never count), measures each included measurement with
 * geometry.measure and its sheet calibration, groups measurements that share
 * assembly.id + assembly.group + layer, and runs each assembly.
 *
 * Calibration trust: when `calibration_checks` is given, each sheet's status
 * comes from geometry.calibrationStatus (checks matched by sheet_id); a
 * 'failed' calibration excludes that sheet's length/area measurements. Without
 * checks, only `cal.verified === true` counts as verified. Unverified scales
 * still produce lines, flagged with an assumption and a warning.
 *
 * Returns {lines, excluded:[{measurement_id, reason}], warnings:[{code, message, ...}]}.
 */
export function computeTakeoff({ measurements = [], calibrations = [], sheets = [], calibration_checks = null } = {}) {
  if (!Array.isArray(measurements)) throw new AssemblyError('computeTakeoff: measurements must be an array');
  if (!Array.isArray(calibrations)) throw new AssemblyError('computeTakeoff: calibrations must be an array');
  if (!Array.isArray(sheets)) throw new AssemblyError('computeTakeoff: sheets must be an array');
  if (calibration_checks !== null && !Array.isArray(calibration_checks)) {
    throw new AssemblyError('computeTakeoff: calibration_checks must be an array when given');
  }

  const resolved = looksResolved(sheets) ? sheets : resolveSheets(sheets);
  const filtered = filterMeasurementsForTakeoff(measurements, resolved);
  const excluded = [...(filtered.excluded ?? [])];
  const warnings = [];

  const calBySheet = new Map();
  for (const cal of calibrations) {
    if (!cal || typeof cal.sheet_id !== 'string') continue;
    if (calBySheet.has(cal.sheet_id)) {
      warnings.push({ code: 'MULTIPLE_CALIBRATIONS', sheet_id: cal.sheet_id, message: `Sheet ${cal.sheet_id} has more than one calibration; the last one listed is used.` });
    }
    calBySheet.set(cal.sheet_id, cal);
  }
  const calTrust = calibrationTrust(calBySheet, calibration_checks, warnings);
  const unverifiedWarned = new Set();

  // 1. Measure each included measurement and bucket it into its group.
  const groups = new Map();
  for (const entry of filtered.included ?? []) {
    const { m, flags } = unwrapIncluded(entry);
    const mid = m?.id;
    const exclude = (reason) => excluded.push({ measurement_id: mid ?? null, reason });
    if (!m || typeof mid !== 'string') { exclude('measurement has no id'); continue; }
    if (!m.assembly || !m.assembly.id) {
      warnings.push({ code: 'NO_ASSEMBLY', measurement_id: mid, message: `Measurement ${mid} has no assembly; it produces no BOM lines.` });
      continue;
    }
    if (m.review === 'rejected') { exclude('rejected in review'); continue; }
    if (m.source === 'ai_suggested' && m.review !== 'accepted') { exclude('AI-suggested measurement has not been accepted in review'); continue; }
    if (!Object.hasOwn(ASSEMBLIES, m.assembly.id)) { exclude(`unknown assembly "${m.assembly.id}"`); continue; }
    const assembly = ASSEMBLIES[m.assembly.id];
    if (!LAYERS.includes(m.layer)) { exclude(`measurement has no valid layer (got ${JSON.stringify(m.layer ?? null)})`); continue; }
    if (!assembly.layer_rule.allowed_layers.includes(m.layer)) {
      exclude(`layer "${m.layer}" not allowed for ${assembly.id} (${assembly.layer_rule.note})`);
      continue;
    }
    const cal = calBySheet.get(m.sheet_id) ?? null;
    if (m.kind !== 'count' && !cal) { exclude(`no calibration for sheet ${m.sheet_id}`); continue; }
    if (m.kind !== 'count' && calTrust.get(m.sheet_id) === 'failed') {
      exclude(`calibration for sheet ${m.sheet_id} failed an independent check; recalibrate before using its quantities`);
      continue;
    }

    let geo;
    let segments;
    try {
      validateMeasurement(m);
      geo = measure(m, cal);
      segments = cal ? linearSegmentsFt(m, cal) : [];
    } catch (err) {
      exclude(`could not measure: ${err.message}`);
      continue;
    }
    if (m.kind !== 'count' && calTrust.get(m.sheet_id) !== 'verified') {
      flags.add('calibration_unverified');
      if (!unverifiedWarned.has(m.sheet_id)) {
        unverifiedWarned.add(m.sheet_id);
        warnings.push({ code: 'CALIBRATION_UNVERIFIED', sheet_id: m.sheet_id, message: `Calibration for sheet ${m.sheet_id} is not independently verified.` });
      }
    }
    if (flags.has('needs_revision_review')) {
      warnings.push({ code: 'NEEDS_REVISION_REVIEW', measurement_id: mid, message: `Measurement ${mid} is on a partially superseded sheet; review against the latest revision.` });
    }

    const groupTag = m.assembly.group !== undefined && m.assembly.group !== null && m.assembly.group !== '' ? `g:${m.assembly.group}` : `m:${mid}`;
    const key = `${assembly.id}\u0000${m.layer}\u0000${groupTag}`;
    if (!groups.has(key)) groups.set(key, { assembly, layer: m.layer, params: m.assembly.params ?? {}, parts: [] });
    groups.get(key).parts.push({
      id: mid,
      sheet_id: m.sheet_id,
      flags,
      params: m.assembly.params ?? {},
      kind: m.kind,
      length_ft: geo.length_ft ?? null,
      area_sf: geo.area_sf ?? null,
      perimeter_ft: geo.perimeter_ft ?? null,
      count: geo.count ?? null,
      segments_ft: segments,
    });
  }

  // 2. Run each group's assembly (input order of first member is preserved).
  const lines = [];
  const seen = new Set();
  for (const group of groups.values()) {
    const ids = group.parts.map((p) => p.id);
    const paramsKey = stableStringify(group.params);
    if (group.parts.some((p) => stableStringify(p.params) !== paramsKey)) {
      for (const mid of ids) excluded.push({ measurement_id: mid, reason: `grouped ${group.assembly.id} measurements have different params` });
      warnings.push({ code: 'GROUP_PARAMS_MISMATCH', measurement_ids: [...ids].sort(), message: 'Measurements in one assembly group must share identical params.' });
      continue;
    }
    const ctx = {
      measurement_ids: ids,
      layer: group.layer,
      sheet_ids: group.parts.map((p) => p.sheet_id).filter((s) => typeof s === 'string'),
      flags: group.parts.flatMap((p) => [...p.flags]),
    };
    let groupLines;
    try {
      groupLines = group.assembly.compute(combineMeasured(group.parts), group.params, ctx);
    } catch (err) {
      const reason = `${group.assembly.id}: ${err.message}`;
      for (const mid of ids) excluded.push({ measurement_id: mid, reason });
      warnings.push({ code: 'ASSEMBLY_ERROR', measurement_ids: [...ids].sort(), message: reason });
      continue;
    }
    for (const line of groupLines) {
      if (seen.has(line.line_id)) {
        warnings.push({ code: 'DUPLICATE_LINE_ID', line_id: line.line_id, message: `Duplicate line ${line.line_id} skipped.` });
        continue;
      }
      seen.add(line.line_id);
      lines.push(line);
    }
  }

  // 3. Cross-revision duplicate detection (same material_key on two revisions of one sheet number).
  const dupWarnings = detectDuplicateItems(lines, resolved);
  if (Array.isArray(dupWarnings)) warnings.push(...dupWarnings);

  return { lines, excluded, warnings };
}

/**
 * Apply contractor overrides [{line_id, qty, reason, by, at}] and return new
 * lines (inputs are not mutated). Each override requires a non-empty reason;
 * the computed quantity is kept in original_qty (first original wins when a
 * line is overridden repeatedly). Overrides naming unknown line_ids throw,
 * unless {strict:false}, in which case they are skipped (see staleOverrides).
 */
export function applyOverrides(lines, overrides = [], { strict = true } = {}) {
  if (!Array.isArray(lines)) throw new AssemblyError('applyOverrides: lines must be an array');
  if (!Array.isArray(overrides)) throw new AssemblyError('applyOverrides: overrides must be an array');
  const out = lines.map((l) => ({ ...l, assumptions: [...(l.assumptions ?? [])] }));
  const index = new Map(out.map((l, i) => [l.line_id, i]));
  for (const ov of overrides) {
    if (!ov || typeof ov !== 'object') throw new AssemblyError('applyOverrides: each override must be an object');
    if (typeof ov.reason !== 'string' || ov.reason.trim() === '') {
      throw new AssemblyError(`applyOverrides: override for ${ov.line_id} requires a non-empty reason`);
    }
    if (typeof ov.qty !== 'number' || !Number.isFinite(ov.qty) || ov.qty < 0) {
      throw new AssemblyError(`applyOverrides: override for ${ov.line_id} needs a finite qty >= 0`);
    }
    if (!index.has(ov.line_id)) {
      if (strict) throw new AssemblyError(`applyOverrides: no line with id ${ov.line_id}`);
      continue;
    }
    const i = index.get(ov.line_id);
    const line = out[i];
    const reason = ov.reason.trim();
    const by = typeof ov.by === 'string' && ov.by.trim() ? ov.by.trim() : null;
    const at = typeof ov.at === 'string' && ov.at.trim() ? ov.at.trim() : null;
    const originalQty = line.original_qty ?? line.qty;
    const originalWithWaste = line.original_qty_with_waste ?? line.qty_with_waste;
    const computedFormula = line.computed_formula ?? line.formula;
    const q = round4(ov.qty);
    const factor = 1 + (line.waste_pct ?? 0) / 100;
    const withWaste = line.unit === 'ea' ? ceilCount(q * factor) : round4(q * factor);
    out[i] = {
      ...line,
      qty: q,
      qty_with_waste: withWaste,
      original_qty: originalQty,
      original_qty_with_waste: originalWithWaste,
      computed_formula: computedFormula,
      formula: `Contractor override${by ? ` by ${by}` : ''}: ${fmt(q)} ${unitLabel(line.unit)} (computed ${fmt(originalQty)} ${unitLabel(line.unit)}; reason: ${reason}). Computed: ${computedFormula}`,
      override: { qty: q, reason, by, at },
      assumptions: [...line.assumptions, `Quantity overridden${by ? ` by ${by}` : ''}${at ? ` at ${at}` : ''}: ${reason}`],
    };
  }
  return out;
}

/** Overrides whose line_id no longer exists in `lines` (e.g. after re-measuring). */
export function staleOverrides(lines, overrides = []) {
  const ids = new Set((lines ?? []).map((l) => l.line_id));
  return (overrides ?? []).filter((ov) => !ids.has(ov?.line_id));
}
