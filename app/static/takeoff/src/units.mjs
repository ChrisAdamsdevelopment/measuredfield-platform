// MeasuredField Remodel Takeoff — typed units (spec §3.1).
//
// Units are typed by dimension. Converting across dimensions (for example
// LF -> SF) is always an error: a length can only become an area through an
// explicit, auditable formula in an assembly, never through a unit cast.
// Packages (box, roll, sheet, bag, ...) are product data, not units, so they
// are deliberately NOT recognised here.
//
// Pure logic: no DOM, no clock, no network, no dependencies.

export const MODULE_VERSION = '0.2.0';

/** Error thrown for unknown units, dimension mismatches and invalid quantities. */
export class UnitError extends Error {
  constructor(message) {
    super(message);
    this.name = 'UnitError';
  }
}

export const DIMENSIONS = Object.freeze(['length', 'area', 'volume', 'count', 'mass']);

/** Base unit code for each dimension. */
export const BASE_UNITS = Object.freeze({
  length: 'ft',
  area: 'sf',
  volume: 'cf',
  count: 'ea',
  mass: 'lb',
});

// Each factor is stored as an exact ratio (num / den) of the dimension's base
// unit so that common conversions such as ft <-> in or cy <-> bf stay exact.
// `toBase` (num / den as a float) is exposed for display and documentation.
const UNIT_DEFINITIONS = [
  // length (base: ft)
  ['in', 'inch', 'in', 'length', 1, 12],
  ['ft', 'linear foot', 'LF', 'length', 1, 1],
  ['yd', 'yard', 'yd', 'length', 3, 1],
  ['mm', 'millimetre', 'mm', 'length', 10, 3048], // 1 ft = 304.8 mm
  ['m', 'metre', 'm', 'length', 10000, 3048], // 1 ft = 0.3048 m
  // area (base: sf)
  ['in2', 'square inch', 'sq in', 'area', 1, 144],
  ['sf', 'square foot', 'SF', 'area', 1, 1],
  ['sy', 'square yard', 'SY', 'area', 9, 1],
  ['m2', 'square metre', 'm²', 'area', 100000000, 9290304], // 1 sf = 0.09290304 m²
  // volume (base: cf)
  ['cf', 'cubic foot', 'CF', 'volume', 1, 1],
  ['cy', 'cubic yard', 'CY', 'volume', 27, 1],
  ['bf', 'board foot', 'BF', 'volume', 1, 12], // 144 in³ = 1/12 cf
  ['m3', 'cubic metre', 'm³', 'volume', 1e12, 28316846592], // 1 cf = 0.028316846592 m³
  ['gal_us', 'US liquid gallon', 'gal', 'volume', 231, 1728], // 231 in³ ≈ 0.133680556 cf
  ['l', 'litre', 'L', 'volume', 1e9, 28316846592],
  // count (base: ea)
  ['ea', 'each', 'EA', 'count', 1, 1],
  // mass (base: lb)
  ['lb', 'pound', 'lb', 'mass', 1, 1],
  ['kg', 'kilogram', 'kg', 'mass', 1e8, 45359237], // 1 lb = 0.45359237 kg
];

const RATIOS = new Map();

/**
 * Frozen unit registry: `{code: {code, label, symbol, dimension, toBase}}`.
 * `toBase` multiplies a value in this unit to get the dimension's base unit.
 */
export const UNITS = Object.freeze(
  Object.fromEntries(
    UNIT_DEFINITIONS.map(([code, label, symbol, dimension, num, den]) => {
      RATIOS.set(code, { num, den });
      return [code, Object.freeze({ code, label, symbol, dimension, toBase: num / den })];
    }),
  ),
);

// Accepted spellings, keyed by the cleaned text (see cleanUnitText).
// `pc` is an alias of `ea`, not a separate unit.
const ALIASES = new Map(
  Object.entries({
    in: ['in', 'inch', 'inches', '"'],
    ft: [
      'ft', 'foot', 'feet', 'lf', 'lin ft', 'linft', 'lin feet', 'ln ft', 'lnft',
      'linear ft', 'linear foot', 'linear feet', 'lineal ft', 'lineal foot', 'lineal feet',
    ],
    yd: ['yd', 'yard', 'yards', 'lin yd'],
    mm: ['mm', 'millimeter', 'millimeters', 'millimetre', 'millimetres'],
    m: ['m', 'meter', 'meters', 'metre', 'metres'],
    in2: ['in2', 'sq in', 'sqin', 'square inch', 'square inches'],
    sf: ['sf', 'sq ft', 'sqft', 'ft2', 'sq feet', 'square foot', 'square feet'],
    sy: ['sy', 'sq yd', 'sqyd', 'yd2', 'square yard', 'square yards'],
    m2: ['m2', 'sq m', 'sqm', 'square meter', 'square meters', 'square metre', 'square metres'],
    cf: ['cf', 'cu ft', 'cuft', 'ft3', 'cubic foot', 'cubic feet'],
    cy: ['cy', 'cu yd', 'cuyd', 'yd3', 'cubic yard', 'cubic yards'],
    bf: ['bf', 'bd ft', 'bdft', 'board ft', 'board foot', 'board feet', 'fbm'],
    m3: ['m3', 'cu m', 'cubic meter', 'cubic meters', 'cubic metre', 'cubic metres'],
    gal_us: ['gal_us', 'gal', 'gals', 'gallon', 'gallons', 'us gal', 'gal us'],
    l: ['l', 'liter', 'liters', 'litre', 'litres'],
    ea: ['ea', 'each', 'pc', 'pcs', 'piece', 'pieces'],
    lb: ['lb', 'lbs', 'pound', 'pounds'],
    kg: ['kg', 'kgs', 'kilogram', 'kilograms'],
  }).flatMap(([code, spellings]) => spellings.map((s) => [s, code])),
);

function cleanUnitText(text) {
  return text
    .normalize('NFKC') // folds '²' -> '2' and '³' -> '3'
    .toLowerCase()
    .replace(/[.^]/g, '') // 'sq. ft.' -> 'sq ft', 'ft^2' -> 'ft2'
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Normalise a unit spelling to its canonical code.
 * 'SF' | 'sq ft' | 'ft2' | 'ft²' -> 'sf'; 'LF' | 'lin ft' -> 'ft'; 'EA' | 'each' | 'pc' -> 'ea';
 * 'CY' | 'yd3' -> 'cy'; 'BF' | 'bd ft' -> 'bf'. Throws UnitError for anything unrecognised,
 * including package words such as 'box' and ambiguous ones such as 'square'.
 */
export function normalizeUnit(text) {
  if (typeof text !== 'string') {
    throw new UnitError(`Unit must be a string, got ${text === null ? 'null' : typeof text}`);
  }
  const code = ALIASES.get(cleanUnitText(text));
  if (!code) {
    throw new UnitError(`Unknown unit "${text}" (packages such as box, roll, sheet or bag are product data, not units)`);
  }
  return code;
}

/** True when `text` normalises to a known unit; never throws. */
export function isKnownUnit(text) {
  return typeof text === 'string' && ALIASES.has(cleanUnitText(text));
}

/** Dimension of a unit ('length' | 'area' | 'volume' | 'count' | 'mass'); throws on unknown units. */
export function dimensionOf(unit) {
  return UNITS[normalizeUnit(unit)].dimension;
}

/** True when both units measure the same dimension; throws UnitError on unknown units. */
export function sameDimension(a, b) {
  return dimensionOf(a) === dimensionOf(b);
}

function requireFiniteNumber(value, label) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new UnitError(`${label} must be a finite number, got ${String(value)}`);
  }
}

/**
 * Convert `value` from one unit to another of the same dimension.
 * Throws UnitError on unknown units, dimension mismatch (e.g. LF -> SF) or a non-finite value.
 */
export function convert(value, from, to) {
  requireFiniteNumber(value, 'Quantity');
  const fromCode = normalizeUnit(from);
  const toCode = normalizeUnit(to);
  const fromDim = UNITS[fromCode].dimension;
  const toDim = UNITS[toCode].dimension;
  if (fromDim !== toDim) {
    throw new UnitError(`Cannot convert ${fromCode} (${fromDim}) to ${toCode} (${toDim}): dimensions differ`);
  }
  if (fromCode === toCode) return value;
  const f = RATIOS.get(fromCode);
  const t = RATIOS.get(toCode);
  return value * (f.num / t.num) * (t.den / f.den);
}

const PACKAGE_EPSILON = 1e-9;

/**
 * Whole packages needed to cover `requiredQty` of `requiredUnit`.
 * `pkg` is product data: `{coverage_qty, coverage_unit}` (e.g. a tile box covering 10.76 sf).
 * Returns `{packages, covered_qty, overage_qty, unit}` with covered/overage in the required unit.
 * Rounds up with a 1e-9 tolerance so floating-point noise never adds a package.
 * Throws UnitError on dimension mismatch, coverage <= 0, or a negative/non-finite requirement.
 */
export function packagesNeeded(requiredQty, requiredUnit, pkg) {
  requireFiniteNumber(requiredQty, 'Required quantity');
  if (requiredQty < 0) throw new UnitError('Required quantity cannot be negative');
  if (!pkg || typeof pkg !== 'object') throw new UnitError('Package coverage is required');
  requireFiniteNumber(pkg.coverage_qty, 'Package coverage_qty');
  if (pkg.coverage_qty <= 0) throw new UnitError('Package coverage_qty must be greater than 0');

  const unit = normalizeUnit(requiredUnit);
  const requiredInCoverageUnit = convert(requiredQty, unit, pkg.coverage_unit); // throws on mismatch
  const ratio = requiredInCoverageUnit / pkg.coverage_qty;
  const packages = Math.max(0, Math.ceil(ratio - PACKAGE_EPSILON));

  const coveredQty = convert(packages * pkg.coverage_qty, pkg.coverage_unit, unit);
  // Within the epsilon the shortfall is floating-point noise, so overage never goes negative.
  const overageQty = Math.max(0, coveredQty - requiredQty);
  return { packages, covered_qty: coveredQty, overage_qty: overageQty, unit };
}

/**
 * Round a stored quantity (default 4 dp, half away from zero).
 * null/undefined stay null — unknown never becomes 0. Throws on non-finite numbers.
 */
export function roundQty(value, decimals = 4) {
  if (value === null || value === undefined) return null;
  requireFiniteNumber(value, 'Quantity');
  const factor = 10 ** decimals;
  const rounded = Math.sign(value) * (Math.round(Math.abs(value) * factor * (1 + Number.EPSILON)) / factor);
  return rounded === 0 ? 0 : rounded; // avoid -0
}

const NUMBER_FORMATS = new Map();

function numberFormat(decimals) {
  if (!NUMBER_FORMATS.has(decimals)) {
    NUMBER_FORMATS.set(decimals, new Intl.NumberFormat('en-US', { maximumFractionDigits: decimals }));
  }
  return NUMBER_FORMATS.get(decimals);
}

/**
 * Display string for a quantity, e.g. formatQty(1234.5, 'ft') -> '1,234.5 LF'.
 * Unknown/null values render as '—' (never '0'). Unrecognised unit text (such as a
 * package label 'box') is shown as given rather than throwing, because this is display only.
 */
export function formatQty(value, unit, { decimals = 2 } = {}) {
  const symbol = isKnownUnit(unit) ? UNITS[normalizeUnit(unit)].symbol : String(unit ?? '').trim();
  const number =
    typeof value === 'number' && Number.isFinite(value)
      ? numberFormat(decimals).format(value === 0 ? 0 : value)
      : '—';
  return symbol ? `${number} ${symbol}` : number;
}
