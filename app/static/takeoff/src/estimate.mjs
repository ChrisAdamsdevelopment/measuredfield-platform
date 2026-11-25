// MeasuredField Remodel Takeoff — priced estimate (architecture v2, section 3.9).
//
// Turns BOM lines (from assemblies.mjs) plus per-line pricing selections into a
// priced estimate. Core rules:
//
// * Unpriced ≠ $0. A line without a usable price keeps `extended_cents: null`,
//   is counted in `totals.missing_lines`, makes `is_complete` false and is
//   displayed as "UNPRICED" / "QUOTE REQUIRED" / "PRICE EXPIRED" — never "$0.00".
// * Money is integer cents. Totals are split into confirmed / provisional /
//   allowance buckets; by-owner lines are excluded from totals and listed.
// * Prices are dated. Retail observations are classified with
//   catalog.priceFreshness(obs, asOf): fresh → PRICED (confirmed),
//   stale → PRICED_STALE (provisional), expired → PRICE_EXPIRED (unpriced).
//   `asOf` is required; there is no implicit "now".
// * Units are typed. A dimension mismatch between a line and its price basis
//   (e.g. a SF line priced with a $/LF rate) is never converted: the line is
//   flagged UNPRICED with an error (code UNIT_MISMATCH) and listed in `errors`.
//
// ROUNDING RULES (documented contract)
// * Package pricing: extended_cents = Math.round(packages × price_cents). Packages
//   are whole numbers and prices integer cents, so this is exact.
// * Per-usable-unit and contractor rates: the priced quantity and the rate are
//   fixed to 4 decimal places, multiplied exactly (BigInt) and rounded half-up
//   to whole cents. This equals Math.round(qty × rate) without floating-point
//   drift (e.g. 2.675 × 100¢ → 268¢, not 267¢).
// * Percentages (tax, markup, contingency): the percent is fixed to 4 decimal
//   places and applied with exact integer arithmetic, rounded half-up.
//
// TOTALS ORDER OF OPERATIONS
//   cost_subtotal_known = confirmed + provisional + allowance + labor + shipping(if set)
//   tax                 = tax_rate_pct × taxable base (null when tax_rate_pct is null)
//   pct_base            = cost_subtotal_known + tax(if known)
//   contingency, markup = each pct × pct_base (they do not compound)
//   grand_total_known   = pct_base + contingency + markup
// Markup and contingency are applied only to KNOWN amounts; when anything is
// missing or provisional the estimate is flagged incomplete and a warning says
// that the percentages were applied to a partial base.

import { UnitError, convert, normalizeUnit, packagesNeeded, sameDimension } from './units.mjs';
import {lineBasis} from './bid-readiness.mjs';
import { normalizedUnitCost, priceFreshness } from './catalog.mjs';
import { QuoteError, quoteContribution, toCalendarDate } from './quotes.mjs';

export const MODULE_VERSION = '0.2.0';

/** Per-line price statuses. */
export const PRICE_STATUS = Object.freeze({
  PRICED: 'PRICED',
  PRICED_STALE: 'PRICED_STALE',
  QUOTE_PENDING: 'QUOTE_PENDING',
  QUOTE_PROVISIONAL: 'QUOTE_PROVISIONAL',
  QUOTE_APPROVED: 'QUOTE_APPROVED',
  ALLOWANCE: 'ALLOWANCE',
  BY_OWNER: 'BY_OWNER',
  UNPRICED: 'UNPRICED',
  PRICE_EXPIRED: 'PRICE_EXPIRED',
});

/** Selection modes accepted per line. */
export const SELECTION_MODES = Object.freeze([
  'retail',
  'contractor_rate',
  'supplier_quote',
  'allowance',
  'by_owner',
  'unpriced',
]);

/** Statuses that count as a missing price (extended_cents null, estimate incomplete). */
const MISSING_STATUSES = new Set([PRICE_STATUS.UNPRICED, PRICE_STATUS.PRICE_EXPIRED, PRICE_STATUS.QUOTE_PENDING]);

/** Statuses whose amount is provisional (stale retail price or a quote that is not approved). */
const PROVISIONAL_STATUSES = new Set([PRICE_STATUS.PRICED_STALE, PRICE_STATUS.QUOTE_PROVISIONAL]);

/**
 * Reasons that indicate contradictory or invalid data (reported in `errors`).
 * Every other missing-price reason is a normal workflow state (reported in `warnings`).
 */
const ERROR_REASONS = new Set([
  'UNIT_MISMATCH',
  'UNKNOWN_UNIT',
  'QTY_UNKNOWN',
  'PRODUCT_NOT_FOUND',
  'OBSERVATION_NOT_FOUND',
  'SUPPLIER_ITEM_NOT_FOUND',
  'PRODUCT_OBSERVATION_MISMATCH',
  'INVALID_PRICE',
  'INVALID_PRICE_BASIS',
  'CURRENCY_UNSUPPORTED',
  'PACKAGE_MISSING',
  'INVALID_PACKAGE',
  'FRESHNESS_UNKNOWN',
  'INVALID_RATE',
  'INVALID_AMOUNT',
  'QUOTE_NOT_FOUND',
  'QUOTE_LINE_MISMATCH',
  'QUOTE_AMOUNT_INVALID',
  'QUOTE_INVALID',
  'AMOUNT_OVERFLOW',
]);

const REASON_MESSAGES = Object.freeze({
  NO_SELECTION: 'No pricing selected for this line',
  EXPLICITLY_UNPRICED: 'Line marked unpriced',
  OBSERVATION_NOT_SELECTED: 'Retail mode needs a price observation (observation_id)',
  OBSERVATION_NOT_FOUND: 'Selected price observation is not in the catalog',
  SUPPLIER_ITEM_NOT_FOUND: 'Price observation refers to an unknown supplier item',
  PRODUCT_NOT_FOUND: 'Selected product is not in the catalog',
  PRODUCT_OBSERVATION_MISMATCH: 'Price observation belongs to a different product',
  INVALID_PRICE: 'Price observation has no valid positive integer price_cents',
  INVALID_PRICE_BASIS: "Price observation 'per' must be 'package' or 'usable_unit'",
  CURRENCY_UNSUPPORTED: 'Only USD prices are supported (no currency conversion is invented)',
  PACKAGE_MISSING: 'Package-priced product has no package coverage',
  INVALID_PACKAGE: 'Product package coverage must be a positive number with a unit',
  FRESHNESS_UNKNOWN: 'Price freshness could not be determined',
  PRICE_EXPIRED: 'Price observation is expired as of the estimate date',
  UNIT_MISMATCH: 'Line unit and price unit measure different dimensions',
  UNKNOWN_UNIT: 'Unit is not a recognised unit',
  QTY_UNKNOWN: 'Line quantity is unknown or invalid',
  RATE_MISSING: 'Contractor rate not entered',
  RATE_UNIT_MISSING: 'Contractor rate unit not entered',
  INVALID_RATE: 'Contractor rate must be a finite number of cents ≥ 0',
  ALLOWANCE_AMOUNT_MISSING: 'Allowance amount not entered',
  INVALID_AMOUNT: 'Amount must be an integer number of cents ≥ 0',
  QUOTE_REQUIRED: 'Supplier quote required',
  QUOTE_NOT_SELECTED: 'No quote selected for this line',
  MULTIPLE_QUOTES_SELECT_ONE: 'Several quotes cover this line; select one',
  QUOTE_NOT_FOUND: 'Selected quote does not exist',
  QUOTE_LINE_MISMATCH: 'Selected quote does not cover this line',
  QUOTE_NOT_RECEIVED: 'Quote not received yet',
  QUOTE_EXPIRED: 'Quote expired; re-request it',
  QUOTE_AMOUNT_INVALID: 'Quote amount is missing or invalid',
  QUOTE_VALIDITY_UNKNOWN: 'Quote has no valid_until date',
  QUOTE_INVALID: 'Quote record is invalid',
  AMOUNT_OVERFLOW: 'Amount exceeds the safe integer range',
});

/** Error thrown for structurally invalid estimate input (missing asOf, bad settings, unknown mode ...). */
export class EstimateError extends Error {
  constructor(message, code = 'INVALID_ESTIMATE_INPUT', details = {}) {
    super(message);
    this.name = 'EstimateError';
    this.code = code;
    this.details = details;
  }
}

// ---------------------------------------------------------------------------
// Money helpers
// ---------------------------------------------------------------------------

const FOUR_DP = 10_000n;

/** Fix a finite non-negative number to 4 decimal places as a BigInt scaled by 10^4. */
function toScaled4(value) {
  return BigInt(Math.round(value * 10_000));
}

/** Non-negative BigInt division rounded half-up. */
function divideHalfUp(numerator, denominator) {
  return (numerator + denominator / 2n) / denominator;
}

function toSafeCents(big, label) {
  const value = Number(big);
  if (!Number.isSafeInteger(value)) {
    throw new EstimateError(`${label} exceeds the safe integer range`, 'AMOUNT_OVERFLOW');
  }
  return value;
}

/** Round a quantity to 4 decimal places for stored output. */
function round4(value) {
  const rounded = Math.round(value * 10_000) / 10_000;
  return rounded === 0 ? 0 : rounded;
}

/**
 * Exact cost of `qty` units at `centsPerUnit` (both fixed to 4 dp), rounded half-up
 * to integer cents. Both arguments must be finite and ≥ 0.
 */
export function multiplyCents(qty, centsPerUnit) {
  if (typeof qty !== 'number' || !Number.isFinite(qty) || qty < 0) {
    throw new EstimateError('quantity must be a finite number ≥ 0', 'INVALID_QTY');
  }
  if (typeof centsPerUnit !== 'number' || !Number.isFinite(centsPerUnit) || centsPerUnit < 0) {
    throw new EstimateError('cents per unit must be a finite number ≥ 0', 'INVALID_RATE');
  }
  return toSafeCents(divideHalfUp(toScaled4(qty) * toScaled4(centsPerUnit), FOUR_DP * FOUR_DP), 'extended cost');
}

/**
 * `pct` percent of integer `cents`, rounded half-up to integer cents
 * (percent fixed to 4 decimal places, exact integer arithmetic).
 */
export function applyPercent(cents, pct) {
  if (!Number.isSafeInteger(cents) || cents < 0) {
    throw new EstimateError('cents must be an integer ≥ 0', 'INVALID_AMOUNT');
  }
  if (typeof pct !== 'number' || !Number.isFinite(pct) || pct < 0) {
    throw new EstimateError('percent must be a finite number ≥ 0', 'INVALID_PERCENT');
  }
  return toSafeCents(divideHalfUp(BigInt(cents) * toScaled4(pct), 100n * FOUR_DP), 'percentage amount');
}

/**
 * Format integer cents as US dollars, e.g. 123456 → '$1,234.56'.
 * Throws for null / non-integers so an unknown amount can never render as '$0.00'.
 */
export function formatCents(cents) {
  if (!Number.isSafeInteger(cents)) {
    throw new EstimateError(`formatCents needs integer cents, got ${String(cents)}`, 'INVALID_AMOUNT');
  }
  const sign = cents < 0 ? '-' : '';
  const abs = Math.abs(cents);
  const dollars = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const rest = String(abs % 100).padStart(2, '0');
  return `${sign}$${dollars}.${rest}`;
}

/**
 * Display text for a priced estimate line. Missing prices show their status
 * ('UNPRICED', 'QUOTE REQUIRED', 'QUOTE EXPIRED', 'PRICE EXPIRED'), never '$0.00'.
 */
export function displayAmount(line) {
  const status = line?.price_status;
  if (line?.quote_allocation === 'included') {
    const qualifier = status === PRICE_STATUS.QUOTE_APPROVED ? 'approved quote' : 'unapproved quote (provisional)';
    return `Included in ${qualifier} — amount on line ${line.included_in_line_id}`;
  }
  switch (status) {
    case PRICE_STATUS.BY_OWNER:
      return 'BY OWNER (excluded)';
    case PRICE_STATUS.UNPRICED:
      return 'UNPRICED';
    case PRICE_STATUS.PRICE_EXPIRED:
      return 'PRICE EXPIRED';
    case PRICE_STATUS.QUOTE_PENDING:
      return line.price_reason === 'QUOTE_EXPIRED' ? 'QUOTE EXPIRED' : 'QUOTE REQUIRED';
    default:
      break;
  }
  if (!Number.isSafeInteger(line?.extended_cents)) return 'UNPRICED';
  const amount = formatCents(line.extended_cents);
  switch (status) {
    case PRICE_STATUS.PRICED:
      return amount;
    case PRICE_STATUS.PRICED_STALE:
      return `${amount} (stale price — provisional)`;
    case PRICE_STATUS.QUOTE_APPROVED:
      return `${amount} (approved quote)`;
    case PRICE_STATUS.QUOTE_PROVISIONAL:
      return `${amount} (quote not approved — provisional)`;
    case PRICE_STATUS.ALLOWANCE:
      return `${amount} (allowance)`;
    default:
      return 'UNPRICED';
  }
}

// ---------------------------------------------------------------------------
// Input validation and indexing
// ---------------------------------------------------------------------------

function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim() !== '';
}

function requireAsOf(asOf) {
  if (asOf === undefined || asOf === null || asOf === '') {
    throw new EstimateError('buildEstimate requires an explicit asOf date (no implicit now)', 'AS_OF_REQUIRED');
  }
  try {
    return toCalendarDate(asOf, 'asOf');
  } catch (err) {
    if (err instanceof QuoteError) throw new EstimateError(err.message, 'INVALID_AS_OF');
    throw err;
  }
}

function validateLines(lines) {
  if (!Array.isArray(lines)) {
    throw new EstimateError('lines must be an array of BOM lines', 'INVALID_LINES');
  }
  const seen = new Set();
  for (const line of lines) {
    if (!line || typeof line !== 'object' || !isNonEmptyString(line.line_id)) {
      throw new EstimateError('every BOM line needs a non-empty line_id', 'INVALID_LINES');
    }
    if (seen.has(line.line_id)) {
      throw new EstimateError(`duplicate line_id "${line.line_id}"`, 'INVALID_LINES', { line_id: line.line_id });
    }
    seen.add(line.line_id);
  }
  return seen;
}

function indexSelections(selections, lineIds, ctx) {
  const map = new Map();
  if (selections === undefined || selections === null) return map;
  let list;
  if (Array.isArray(selections)) {
    list = selections;
  } else if (typeof selections === 'object') {
    list = Object.entries(selections).map(([key, value]) => {
      if (value && value.line_id !== undefined && value.line_id !== key) {
        throw new EstimateError(`selection key "${key}" does not match its line_id "${value.line_id}"`, 'INVALID_SELECTION');
      }
      return { ...value, line_id: key };
    });
  } else {
    throw new EstimateError('selections must be an array or an object keyed by line_id', 'INVALID_SELECTION');
  }
  for (const sel of list) {
    if (!sel || typeof sel !== 'object' || !isNonEmptyString(sel.line_id)) {
      throw new EstimateError('every selection needs a line_id', 'INVALID_SELECTION');
    }
    if (!SELECTION_MODES.includes(sel.mode)) {
      throw new EstimateError(`unknown selection mode "${sel.mode}" for line ${sel.line_id}`, 'INVALID_SELECTION', {
        line_id: sel.line_id,
      });
    }
    if (sel.qty_basis !== undefined && sel.qty_basis !== 'with_waste' && sel.qty_basis !== 'net') {
      throw new EstimateError(`qty_basis must be 'with_waste' or 'net' (line ${sel.line_id})`, 'INVALID_SELECTION');
    }
    if (map.has(sel.line_id)) {
      throw new EstimateError(`more than one selection for line ${sel.line_id}`, 'INVALID_SELECTION', { line_id: sel.line_id });
    }
    if (!lineIds.has(sel.line_id)) {
      ctx.warn('warning', 'SELECTION_FOR_UNKNOWN_LINE', sel.line_id, 'Selection refers to a line that is not in this takeoff');
    }
    map.set(sel.line_id, sel);
  }
  return map;
}

function indexById(list, label) {
  const map = new Map();
  if (list === undefined || list === null) return map;
  if (!Array.isArray(list)) throw new EstimateError(`catalog.${label} must be an array`, 'INVALID_CATALOG');
  for (const item of list) {
    if (item && isNonEmptyString(item.id)) map.set(item.id, item);
  }
  return map;
}

function indexQuotes(quotes) {
  const byId = new Map();
  const byLine = new Map();
  if (quotes === undefined || quotes === null) return { byId, byLine };
  if (!Array.isArray(quotes)) throw new EstimateError('quotes must be an array', 'INVALID_QUOTES');
  for (const quote of quotes) {
    if (!quote || !isNonEmptyString(quote.id) || !Array.isArray(quote.line_ids)) {
      throw new EstimateError('every quote needs an id and a line_ids array', 'INVALID_QUOTES');
    }
    if (byId.has(quote.id)) throw new EstimateError(`duplicate quote id "${quote.id}"`, 'INVALID_QUOTES');
    byId.set(quote.id, quote);
    for (const lineId of quote.line_ids) {
      if (!byLine.has(lineId)) byLine.set(lineId, []);
      byLine.get(lineId).push(quote);
    }
  }
  return { byId, byLine };
}

function percentSetting(value, label, { max }) {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > max) {
    throw new EstimateError(`settings.${label} must be a number between 0 and ${max}`, 'INVALID_SETTINGS', { field: label });
  }
  return value;
}

function validateSettings(settings) {
  const s = settings ?? {};
  if (typeof s !== 'object') throw new EstimateError('settings must be an object', 'INVALID_SETTINGS');
  const shipping = s.shipping_cents;
  if (shipping !== undefined && shipping !== null && (!Number.isSafeInteger(shipping) || shipping < 0)) {
    throw new EstimateError('settings.shipping_cents must be integer cents ≥ 0 or null', 'INVALID_SETTINGS', {
      field: 'shipping_cents',
    });
  }
  if (s.labor !== undefined && s.labor !== null && !Array.isArray(s.labor)) {
    throw new EstimateError('settings.labor must be an array', 'INVALID_SETTINGS', { field: 'labor' });
  }
  return {
    tax_rate_pct: percentSetting(s.tax_rate_pct, 'tax_rate_pct', { max: 100 }),
    markup_pct: percentSetting(s.markup_pct, 'markup_pct', { max: 1000 }) ?? 0,
    contingency_pct: percentSetting(s.contingency_pct, 'contingency_pct', { max: 1000 }) ?? 0,
    shipping_cents: shipping ?? null,
    labor: s.labor ?? [],
  };
}

// ---------------------------------------------------------------------------
// Per-line pricing
// ---------------------------------------------------------------------------

function emptyResult(mode) {
  return {
    pricing_mode: mode,
    price_status: null,
    price_bucket: null,
    price_reason: null,
    price_detail: null,
    packages: null,
    package_label: null,
    priced_qty: null,
    priced_unit: null,
    unit_cost_cents: null,
    unit_cost_basis: null,
    normalized_unit_cost: null,
    extended_cents: null,
    freshness: null,
    quote_id: null,
    quote_allocation: null,
    included_in_line_id: null,
    taxable: false,
    provenance: null,
  };
}

/** Mark a result as missing a price (extended stays null) and return it. */
function markMissing(result, status, reason, detail = null) {
  result.price_status = status;
  result.price_reason = reason;
  result.price_detail = detail;
  result.price_bucket = null;
  result.extended_cents = null;
  return result;
}

/** Normalize a unit or return null when it is not a recognised unit. */
function tryUnit(text) {
  if (typeof text !== 'string') return null;
  try {
    return normalizeUnit(text);
  } catch {
    return null;
  }
}

/** Quantity to buy: qty_with_waste by default, or net qty when selection.qty_basis === 'net'. */
function purchaseQty(line, selection) {
  const basis = selection?.qty_basis ?? 'with_waste';
  const value = basis === 'net' || line.qty_with_waste === undefined ? line.qty : line.qty_with_waste;
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function priceRetail(line, sel, ctx, r) {
  if (!isNonEmptyString(sel.observation_id)) return markMissing(r, PRICE_STATUS.UNPRICED, 'OBSERVATION_NOT_SELECTED');
  const obs = ctx.observations.get(sel.observation_id);
  if (!obs) return markMissing(r, PRICE_STATUS.UNPRICED, 'OBSERVATION_NOT_FOUND', sel.observation_id);
  const item = ctx.supplierItems.get(obs.supplier_item_id);
  if (!item) return markMissing(r, PRICE_STATUS.UNPRICED, 'SUPPLIER_ITEM_NOT_FOUND', obs.supplier_item_id ?? null);
  if (isNonEmptyString(sel.product_id) && item.product_id !== sel.product_id) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'PRODUCT_OBSERVATION_MISMATCH', `${sel.product_id} ≠ ${item.product_id}`);
  }
  const product = ctx.products.get(item.product_id);
  if (!product) return markMissing(r, PRICE_STATUS.UNPRICED, 'PRODUCT_NOT_FOUND', item.product_id ?? null);

  r.provenance = {
    source_type: obs.source_type ?? null,
    observed_at: obs.observed_at ?? null,
    supplier: item.supplier_name ?? null,
    store_id: obs.store_id ?? null,
    source_ref: obs.source_ref ?? null,
    source_license: obs.source_license ?? null,
    reliability: obs.reliability ?? null,
    availability: obs.availability ?? null,
    observation_id: obs.id,
    supplier_item_id: item.id,
    product_id: product.id,
  };

  if (isNonEmptyString(product.material_key) && isNonEmptyString(line.material_key) && product.material_key !== line.material_key) {
    ctx.warn('warning', 'MATERIAL_KEY_MISMATCH', line.line_id,
      `Selected product is for "${product.material_key}" but the line is "${line.material_key}"`);
  }
  if (obs.currency !== 'USD') return markMissing(r, PRICE_STATUS.UNPRICED, 'CURRENCY_UNSUPPORTED', String(obs.currency ?? 'missing'));
  if (!Number.isSafeInteger(obs.price_cents) || obs.price_cents <= 0) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'INVALID_PRICE', String(obs.price_cents));
  }
  if (obs.per !== 'package' && obs.per !== 'usable_unit') {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'INVALID_PRICE_BASIS', String(obs.per));
  }

  // Units and quantity first: a contradictory unit is a data error even for an expired price.
  const lineUnit = tryUnit(line.unit);
  if (!lineUnit) return markMissing(r, PRICE_STATUS.UNPRICED, 'UNKNOWN_UNIT', `line unit "${line.unit}"`);
  const qty = purchaseQty(line, sel);
  if (qty === null) return markMissing(r, PRICE_STATUS.UNPRICED, 'QTY_UNKNOWN');

  const pkg = product.package ?? null;
  const hasPackage = pkg !== null && typeof pkg === 'object' && pkg.coverage_qty !== undefined && pkg.coverage_qty !== null;
  let coverageUnit = null;
  if (hasPackage) {
    coverageUnit = tryUnit(pkg.coverage_unit);
    if (!coverageUnit) return markMissing(r, PRICE_STATUS.UNPRICED, 'UNKNOWN_UNIT', `package unit "${pkg.coverage_unit}"`);
    if (typeof pkg.coverage_qty !== 'number' || !Number.isFinite(pkg.coverage_qty) || pkg.coverage_qty <= 0) {
      return markMissing(r, PRICE_STATUS.UNPRICED, 'INVALID_PACKAGE', String(pkg.coverage_qty));
    }
    if (!sameDimension(lineUnit, coverageUnit)) {
      return markMissing(r, PRICE_STATUS.UNPRICED, 'UNIT_MISMATCH', `line ${lineUnit} vs package coverage ${coverageUnit}`);
    }
  }
  let usableUnit = null;
  if (obs.per === 'usable_unit') {
    usableUnit = tryUnit(product.usable_unit);
    if (!usableUnit) return markMissing(r, PRICE_STATUS.UNPRICED, 'UNKNOWN_UNIT', `usable unit "${product.usable_unit}"`);
    if (!sameDimension(lineUnit, usableUnit)) {
      return markMissing(r, PRICE_STATUS.UNPRICED, 'UNIT_MISMATCH', `line ${lineUnit} vs price per ${usableUnit}`);
    }
  } else if (!hasPackage) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'PACKAGE_MISSING');
  }

  // Freshness is decided by the catalog policy for the explicit as-of calendar date.
  let freshness;
  try {
    freshness = priceFreshness(obs, ctx.asOf);
  } catch (err) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'FRESHNESS_UNKNOWN', err?.message ?? null);
  }
  if (!['fresh', 'stale', 'expired'].includes(freshness)) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'FRESHNESS_UNKNOWN', String(freshness));
  }
  r.freshness = freshness;
  r.provenance.freshness = freshness;
  if (freshness === 'expired') return markMissing(r, PRICE_STATUS.PRICE_EXPIRED, 'PRICE_EXPIRED', obs.observed_at ?? null);

  if (hasPackage) {
    const need = packagesNeeded(qty, lineUnit, { coverage_qty: pkg.coverage_qty, coverage_unit: coverageUnit });
    r.packages = need.packages;
    r.package_label = isNonEmptyString(pkg.label) ? pkg.label : 'package';
  }
  if (obs.per === 'package') {
    r.priced_qty = r.packages;
    r.priced_unit = 'package';
    r.unit_cost_cents = obs.price_cents;
    r.unit_cost_basis = 'package';
    r.extended_cents = Math.round(r.packages * obs.price_cents); // documented rule; exact for integers
  } else {
    // Priced per usable unit; when the product is sold in packages, whole packages are bought.
    const billed = hasPackage
      ? convert(r.packages * pkg.coverage_qty, coverageUnit, usableUnit)
      : convert(qty, lineUnit, usableUnit);
    r.priced_qty = round4(billed);
    r.priced_unit = usableUnit;
    r.unit_cost_cents = obs.price_cents;
    r.unit_cost_basis = 'usable_unit';
    r.extended_cents = multiplyCents(r.priced_qty, obs.price_cents);
  }
  try {
    r.normalized_unit_cost = normalizedUnitCost(obs, product);
  } catch {
    r.normalized_unit_cost = null;
  }

  r.price_status = freshness === 'fresh' ? PRICE_STATUS.PRICED : PRICE_STATUS.PRICED_STALE;
  r.price_bucket = freshness === 'fresh' ? 'confirmed' : 'provisional';
  if (typeof sel.taxable === 'boolean') {
    r.taxable = sel.taxable;
  } else {
    r.taxable = obs.tax_included !== true && line.price_class !== 'labor_only';
  }
  if (obs.tax_included === true && sel.taxable !== true) {
    ctx.warn('info', 'TAX_INCLUDED_IN_PRICE', line.line_id, 'Observed price already includes tax; not taxed again');
  }
  if (freshness === 'stale') {
    ctx.warn('warning', 'STALE_PRICE', line.line_id,
      `Price observed ${obs.observed_at} is stale as of ${ctx.asOf}; counted as provisional`);
  }
  if (obs.availability === 'out_of_stock' || obs.availability === 'limited') {
    ctx.warn('warning', 'AVAILABILITY', line.line_id, `Observed availability: ${obs.availability}`);
  }
  if (r.packages === 0 || qty === 0) {
    ctx.warn('info', 'ZERO_QTY', line.line_id, 'Line quantity is zero');
  }
  return r;
}

function priceContractorRate(line, sel, ctx, r) {
  const rate = sel.rate_cents_per_unit;
  if (rate === undefined || rate === null) return markMissing(r, PRICE_STATUS.UNPRICED, 'RATE_MISSING');
  if (typeof rate !== 'number' || !Number.isFinite(rate) || rate < 0) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'INVALID_RATE', String(rate));
  }
  if (!isNonEmptyString(sel.rate_unit)) return markMissing(r, PRICE_STATUS.UNPRICED, 'RATE_UNIT_MISSING');
  const rateUnit = tryUnit(sel.rate_unit);
  if (!rateUnit) return markMissing(r, PRICE_STATUS.UNPRICED, 'UNKNOWN_UNIT', `rate unit "${sel.rate_unit}"`);
  const lineUnit = tryUnit(line.unit);
  if (!lineUnit) return markMissing(r, PRICE_STATUS.UNPRICED, 'UNKNOWN_UNIT', `line unit "${line.unit}"`);
  if (!sameDimension(lineUnit, rateUnit)) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'UNIT_MISMATCH', `line ${lineUnit} vs rate per ${rateUnit}`);
  }
  const qty = purchaseQty(line, sel);
  if (qty === null) return markMissing(r, PRICE_STATUS.UNPRICED, 'QTY_UNKNOWN');

  r.priced_qty = round4(convert(qty, lineUnit, rateUnit));
  r.priced_unit = rateUnit;
  r.unit_cost_cents = rate;
  r.unit_cost_basis = 'rate_unit';
  r.extended_cents = multiplyCents(r.priced_qty, rate);
  r.price_status = PRICE_STATUS.PRICED;
  r.price_bucket = 'confirmed';
  r.taxable = sel.taxable === true;
  r.provenance = {
    source_type: 'contractor_rate',
    observed_at: sel.rate_date ?? null,
    supplier: sel.supplier ?? null,
    store_id: null,
    source_ref: sel.source_ref ?? 'contractor-entered rate',
  };
  if (rate === 0) ctx.warn('warning', 'ZERO_RATE', line.line_id, 'Contractor rate entered as 0');
  return r;
}

function priceAllowance(line, sel, ctx, r) {
  // Quantity-derived allowances must be reviewed again if the basis changes.
  // Legacy fixed allowances remain supported; legacy generated allowances are not trusted.
  if ((sel?.allowance_basis === 'quantity_bound' || sel?.quantity_basis != null ||
      /^PRELIMINARY unconfirmed retail observation/.test(sel?.source_ref || '')) &&
      sel.quantity_basis !== lineBasis(line)) {
    return markMissing(r, PRICE_STATUS.UNPRICED, 'ALLOWANCE_BASIS_CHANGED', 'Reconfirm quantity and package pricing');
  }
  const amount = sel?.allowance_cents ?? line.allowance_cents;
  if (amount === undefined || amount === null) return markMissing(r, PRICE_STATUS.UNPRICED, 'ALLOWANCE_AMOUNT_MISSING');
  if (!Number.isSafeInteger(amount) || amount < 0) return markMissing(r, PRICE_STATUS.UNPRICED, 'INVALID_AMOUNT', String(amount));
  r.extended_cents = amount;
  r.price_status = PRICE_STATUS.ALLOWANCE;
  r.price_bucket = 'allowance';
  r.taxable = sel?.taxable === true;
  r.provenance = {
    source_type: 'allowance',
    observed_at: null,
    supplier: null,
    store_id: null,
    source_ref: sel?.source_ref ?? 'contractor allowance',
  };
  if (amount === 0) ctx.warn('warning', 'ZERO_ALLOWANCE', line.line_id, 'Allowance entered as 0');
  return r;
}

function priceQuote(line, sel, ctx, r) {
  let quote;
  if (isNonEmptyString(sel?.quote_id)) {
    quote = ctx.quotes.get(sel.quote_id);
    if (!quote) return markMissing(r, PRICE_STATUS.QUOTE_PENDING, 'QUOTE_NOT_FOUND', sel.quote_id);
  } else {
    const covering = ctx.quotesByLine.get(line.line_id) ?? [];
    if (covering.length === 0) return markMissing(r, PRICE_STATUS.QUOTE_PENDING, 'QUOTE_REQUIRED');
    if (covering.length > 1) {
      return markMissing(r, PRICE_STATUS.QUOTE_PENDING, 'MULTIPLE_QUOTES_SELECT_ONE', covering.map((q) => q.id).join(', '));
    }
    quote = covering[0];
    r.quote_auto_matched = true;
  }
  r.quote_id = quote.id;
  if (!quote.line_ids.includes(line.line_id)) {
    return markMissing(r, PRICE_STATUS.QUOTE_PENDING, 'QUOTE_LINE_MISMATCH', quote.id);
  }

  let contribution;
  try {
    contribution = quoteContribution(quote, ctx.asOf);
  } catch (err) {
    if (err instanceof QuoteError) return markMissing(r, PRICE_STATUS.QUOTE_PENDING, 'QUOTE_INVALID', err.message);
    throw err;
  }
  r.quote_effective_status = contribution.effective_status;
  // Provenance never includes the private attachment_ref.
  r.provenance = {
    source_type: 'supplier_quote',
    observed_at: quote.received_at ?? null,
    supplier: quote.vendor_name ?? null,
    store_id: null,
    source_ref: `quote:${quote.id}`,
    valid_until: quote.valid_until ?? null,
    quote_status: contribution.effective_status,
  };
  if (contribution.bucket === null) return markMissing(r, PRICE_STATUS.QUOTE_PENDING, contribution.reason, quote.id);

  r.price_status = contribution.price_status;
  r.price_bucket = contribution.bucket;
  r.quote_amount_cents = contribution.amount_cents; // allocated to one line in allocateQuotes()
  r.taxable = sel?.taxable === true;
  return r;
}

/**
 * Decide how a line without a selection is treated:
 * by_owner → BY_OWNER; supplier_quote (or covered by a quote) → quote lookup;
 * allowance → the line's own allowance_cents (missing amount stays UNPRICED);
 * everything else → UNPRICED (NO_SELECTION). Nothing is ever defaulted to a price.
 */
function defaultMode(line, ctx) {
  if (line.price_class === 'by_owner') return 'by_owner';
  if (line.price_class === 'supplier_quote' || ctx.quotesByLine.has(line.line_id)) return 'supplier_quote';
  if (line.price_class === 'allowance') return 'allowance';
  return null;
}

function priceByMode(mode, line, sel, ctx, r) {
  switch (mode) {
    case 'retail':
      return priceRetail(line, sel, ctx, r);
    case 'contractor_rate':
      return priceContractorRate(line, sel, ctx, r);
    case 'supplier_quote':
      return priceQuote(line, sel, ctx, r);
    case 'allowance':
      return priceAllowance(line, sel, ctx, r);
    case 'by_owner':
      r.price_status = PRICE_STATUS.BY_OWNER;
      r.price_reason = 'BY_OWNER';
      return r;
    case 'unpriced':
      return markMissing(r, PRICE_STATUS.UNPRICED, 'EXPLICITLY_UNPRICED');
    default:
      return markMissing(r, PRICE_STATUS.UNPRICED, 'NO_SELECTION');
  }
}

function priceLine(line, sel, ctx) {
  const mode = sel ? sel.mode : defaultMode(line, ctx);
  const r = emptyResult(mode ?? 'unpriced');
  r.selection_source = sel ? 'selection' : 'default';
  r.operation_id = sel?.operation_id ?? line.operation_id ?? null;
  r.cost_inclusion = sel?.cost_inclusion ?? line.cost_inclusion ?? null;

  try {
    priceByMode(mode, line, sel, ctx, r);
  } catch (err) {
    // Unit and overflow failures make this one line unpriced; they never become $0.
    if (err instanceof UnitError) {
      markMissing(r, PRICE_STATUS.UNPRICED, 'UNIT_MISMATCH', err.message);
    } else if (err instanceof EstimateError && err.code === 'AMOUNT_OVERFLOW') {
      markMissing(r, PRICE_STATUS.UNPRICED, 'AMOUNT_OVERFLOW', err.message);
    } else {
      throw err;
    }
    r.packages = null;
    r.priced_qty = null;
    r.unit_cost_cents = null;
  }

  if (
    line.price_class === 'supplier_quote' &&
    ['retail', 'contractor_rate', 'allowance'].includes(mode) &&
    r.extended_cents !== null
  ) {
    ctx.warn('warning', 'QUOTE_CLASS_PRICED_WITHOUT_QUOTE', line.line_id,
      'Line normally requires a written supplier quote but is priced another way');
  }
  return r;
}

/**
 * A multi-line quote is one lump sum: its amount is put on the first of its
 * line_ids (in quote order) that resolved to it; the other covered lines are
 * marked 'included' with extended_cents null so the amount is counted once.
 */
function allocateQuotes(results, lines, ctx) {
  const groups = new Map();
  results.forEach((r, index) => {
    if (r.quote_amount_cents === undefined) return;
    if (!groups.has(r.quote_id)) groups.set(r.quote_id, new Map());
    groups.get(r.quote_id).set(lines[index].line_id, r);
  });
  const resultByLine = new Map(lines.map((line, i) => [line.line_id, results[i]]));

  for (const [quoteId, members] of groups) {
    const quote = ctx.quotes.get(quoteId);
    const primaryId = quote.line_ids.find((id) => members.has(id));
    for (const [lineId, r] of members) {
      if (lineId === primaryId) {
        r.quote_allocation = 'primary';
        r.extended_cents = r.quote_amount_cents;
      } else {
        r.quote_allocation = 'included';
        r.included_in_line_id = primaryId;
        r.extended_cents = null;
        r.taxable = false;
      }
    }
    for (const coveredId of quote.line_ids) {
      const other = resultByLine.get(coveredId);
      if (!other) {
        ctx.warn('warning', 'QUOTE_REFERENCES_UNKNOWN_LINE', coveredId, `Quote ${quoteId} covers a line that is not in this takeoff`);
      } else if (!members.has(coveredId) && other.extended_cents !== null) {
        ctx.warn('warning', 'POSSIBLE_DOUBLE_COUNT', coveredId,
          `Line is priced separately but is also covered by quote ${quoteId}`);
      }
    }
  }
}

function priceLabor(items, ctx) {
  return items.map((item, index) => {
    const id = isNonEmptyString(item?.id) ? item.id : `labor_${index + 1}`;
    const out = {
      id,
      description: item?.description ?? null,
      operation_id: item?.operation_id ?? null,
      hours: item?.hours ?? null,
      rate_cents_per_hour: item?.rate_cents_per_hour ?? null,
      amount_cents: null,
      status: PRICE_STATUS.UNPRICED,
      reason: null,
    };
    if (!item || typeof item !== 'object') {
      out.reason = 'INVALID_LABOR';
      ctx.error('INVALID_LABOR', null, `Labor item ${id} is not an object`);
      return out;
    }
    if (item.amount_cents !== undefined && item.amount_cents !== null) {
      if (!Number.isSafeInteger(item.amount_cents) || item.amount_cents < 0) {
        out.reason = 'INVALID_AMOUNT';
        ctx.error('INVALID_LABOR', null, `Labor item ${id}: amount_cents must be integer cents ≥ 0`);
        return out;
      }
      out.amount_cents = item.amount_cents;
    } else if (item.hours !== undefined && item.hours !== null && item.rate_cents_per_hour !== undefined && item.rate_cents_per_hour !== null) {
      const valid = (v) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
      if (!valid(item.hours) || !valid(item.rate_cents_per_hour)) {
        out.reason = 'INVALID_RATE';
        ctx.error('INVALID_LABOR', null, `Labor item ${id}: hours and rate must be finite numbers ≥ 0`);
        return out;
      }
      out.amount_cents = multiplyCents(item.hours, item.rate_cents_per_hour);
    } else {
      out.reason = 'LABOR_AMOUNT_MISSING';
      ctx.warn('warning', 'LABOR_UNPRICED', null, `Labor item ${id} has no amount or hours × rate`);
      return out;
    }
    out.status = PRICE_STATUS.PRICED;
    return out;
  });
}

// ---------------------------------------------------------------------------
// Estimate
// ---------------------------------------------------------------------------

function sumSafe(values, label) {
  let total = 0;
  for (const v of values) total += v;
  if (!Number.isSafeInteger(total)) throw new EstimateError(`${label} exceeds the safe integer range`, 'AMOUNT_OVERFLOW');
  return total;
}

function buildDisplay(totals) {
  const notes = [];
  if (totals.is_complete) {
    notes.push('Complete: every line is priced from a confirmed source, allowance or approved quote.');
  } else {
    notes.push('INCOMPLETE ESTIMATE — the total covers known amounts only.');
  }
  if (totals.missing_lines > 0) {
    notes.push(`${totals.missing_lines} line(s) unpriced, price-expired or awaiting a supplier quote (not counted as $0).`);
  }
  if (totals.provisional_lines > 0) {
    notes.push(`${totals.provisional_lines} line(s) provisional (stale price or quote not approved): ${formatCents(totals.provisional_cents)}.`);
  }
  if (totals.allowance_lines > 0) notes.push(`Allowances included: ${formatCents(totals.allowance_cents)}.`);
  if (totals.missing_labor_items > 0) notes.push(`${totals.missing_labor_items} labor item(s) without an amount.`);
  if (totals.tax_cents === null) notes.push('Sales tax not set — tax is not included.');
  if (totals.shipping_cents === null) notes.push('Shipping/delivery not set — not included.');
  if (totals.by_owner_lines > 0) notes.push(`${totals.by_owner_lines} by-owner line(s) excluded from totals.`);
  if (!totals.is_complete && (totals.markup_cents > 0 || totals.contingency_cents > 0)) {
    notes.push('Markup and contingency were applied to known amounts only.');
  }

  const knownSomething =
    totals.confirmed_cents + totals.provisional_cents + totals.allowance_cents + totals.labor_cents + (totals.shipping_cents ?? 0) > 0 ||
    totals.priced_lines > 0;
  let displayTotal;
  if (totals.is_complete) {
    displayTotal = formatCents(totals.grand_total_known_cents);
  } else if (totals.line_count === 0 && totals.labor_items === 0) {
    displayTotal = 'Nothing to price yet';
  } else if (!knownSomething) {
    displayTotal = totals.missing_lines > 0 ? `Not priced — ${totals.missing_lines} line(s) missing` : 'Not priced';
  } else {
    displayTotal = `${formatCents(totals.grand_total_known_cents)} known so far — INCOMPLETE`;
  }
  return { display_note: notes.join(' '), display_total: displayTotal };
}

/**
 * Build a priced estimate.
 *
 * @param {object} input
 * @param {object[]} input.lines      BOM lines from assemblies (line_id, qty, qty_with_waste, unit, price_class ...)
 * @param {object[]|object} [input.selections]  per-line `{line_id, mode, product_id?, observation_id?,
 *        rate_cents_per_unit?, rate_unit?, quote_id?, allowance_cents?, qty_basis?, taxable?}`
 *        (array, or object keyed by line_id). Lines without a selection default by price_class:
 *        by_owner → BY_OWNER; supplier_quote (or covered by exactly one quote) → quote;
 *        allowance → line.allowance_cents (UNPRICED when absent); otherwise UNPRICED.
 * @param {{products?, supplierItems?, observations?}} [input.catalog]
 * @param {object[]} [input.quotes]   quotes from quotes.mjs
 * @param {object} [input.settings]   `{tax_rate_pct|null, markup_pct, contingency_pct, shipping_cents|null, labor:[]}`
 * @param {string|Date} input.asOf    REQUIRED pricing date (YYYY-MM-DD or ISO); no implicit now
 * @returns {{module_version, as_of, lines, labor, totals, missing, by_owner, warnings, errors}}
 */
export function buildEstimate({ lines, selections, catalog, quotes, settings, asOf } = {}) {
  const asOfDay = requireAsOf(asOf);
  const lineIds = validateLines(lines);
  const config = validateSettings(settings);

  const warnings = [];
  const errors = [];
  const ctx = {
    // One calendar day governs price freshness and quote expiry for the whole estimate.
    asOf: asOfDay,
    warn(level, code, lineId, message) {
      warnings.push({ level, code, line_id: lineId ?? null, message });
    },
    error(code, lineId, message) {
      errors.push({ code, line_id: lineId ?? null, message });
    },
  };

  if (catalog !== undefined && catalog !== null && typeof catalog !== 'object') {
    throw new EstimateError('catalog must be an object', 'INVALID_CATALOG');
  }
  ctx.products = indexById(catalog?.products, 'products');
  ctx.supplierItems = indexById(catalog?.supplierItems, 'supplierItems');
  ctx.observations = indexById(catalog?.observations, 'observations');
  const quoteIndex = indexQuotes(quotes);
  ctx.quotes = quoteIndex.byId;
  ctx.quotesByLine = quoteIndex.byLine;
  const selectionMap = indexSelections(selections, lineIds, ctx);

  // 1. Price each line independently.
  const results = lines.map((line) => priceLine(line, selectionMap.get(line.line_id), ctx));
  // 2. Lump-sum quotes covering several lines are counted once.
  allocateQuotes(results, lines, ctx);

  // 3. Assemble output lines and report missing / provisional states.
  const missing = [];
  const byOwner = [];
  const outLines = lines.map((line, index) => {
    const { quote_amount_cents: _internal, ...pricing } = results[index];
    const out = { ...line, ...pricing };
    out.display_amount = displayAmount(out);

    if (MISSING_STATUSES.has(out.price_status)) {
      const message = REASON_MESSAGES[out.price_reason] ?? out.price_reason;
      const text = out.price_detail ? `${message} (${out.price_detail})` : message;
      missing.push({ line_id: line.line_id, description: line.description ?? null, price_status: out.price_status, reason: out.price_reason, message: text });
      if (ERROR_REASONS.has(out.price_reason)) ctx.error(out.price_reason, line.line_id, text);
      else ctx.warn('warning', out.price_reason, line.line_id, text);
    } else if (out.price_status === PRICE_STATUS.QUOTE_PROVISIONAL && out.quote_allocation === 'primary') {
      ctx.warn('warning', 'QUOTE_NOT_APPROVED', line.line_id, `Quote ${out.quote_id} received but not approved; counted as provisional`);
    } else if (out.price_status === PRICE_STATUS.BY_OWNER) {
      byOwner.push({
        line_id: line.line_id,
        material_key: line.material_key ?? null,
        description: line.description ?? null,
        qty: line.qty_with_waste ?? line.qty ?? null,
        unit: line.unit ?? null,
      });
    }
    return out;
  });

  // Open "needs" (engineer spec, field verification, owner selection) are surfaced once per kind.
  const needs = new Map();
  for (const line of lines) {
    for (const need of Array.isArray(line.needs) ? line.needs : []) {
      if (!needs.has(need)) needs.set(need, []);
      needs.get(need).push(line.line_id);
    }
  }
  for (const [need, ids] of needs) {
    ctx.warn(need === 'engineer_spec' ? 'warning' : 'info', `NEEDS_${String(need).toUpperCase()}`, null,
      `Open item "${need}" on line(s): ${ids.join(', ')}`);
  }
  // Quantity-quality flags from takeoff (clouded revision, unverified calibration) are carried forward.
  const flagged = new Map();
  for (const line of lines) {
    for (const flag of Array.isArray(line.flags) ? line.flags : []) {
      if (!flagged.has(flag)) flagged.set(flag, []);
      flagged.get(flag).push(line.line_id);
    }
  }
  for (const [flag, ids] of flagged) {
    ctx.warn('warning', `LINE_FLAG_${String(flag).toUpperCase()}`, null,
      `Quantity flag "${flag}" on line(s): ${ids.join(', ')}`);
  }

  const labor = priceLabor(config.labor, ctx);

  // 4. Totals.
  const amounts = (bucket) => outLines.filter((l) => l.price_bucket === bucket && l.extended_cents !== null).map((l) => l.extended_cents);
  const confirmed = sumSafe(amounts('confirmed'), 'confirmed total');
  const provisional = sumSafe(amounts('provisional'), 'provisional total');
  const allowance = sumSafe(amounts('allowance'), 'allowance total');
  const laborCents = sumSafe(labor.filter((l) => l.amount_cents !== null).map((l) => l.amount_cents), 'labor total');
  const shipping = config.shipping_cents;
  const taxableBase = sumSafe(
    outLines.filter((l) => l.taxable && l.extended_cents !== null && l.price_bucket !== null).map((l) => l.extended_cents),
    'taxable base',
  );

  const missingLines = outLines.filter((l) => MISSING_STATUSES.has(l.price_status)).length;
  const provisionalLines = outLines.filter((l) => PROVISIONAL_STATUSES.has(l.price_status)).length;
  const missingLabor = labor.filter((l) => l.amount_cents === null).length;
  const pricedLines = outLines.filter((l) => l.price_bucket !== null).length;

  let taxCents = null;
  if (config.tax_rate_pct === null) {
    ctx.warn(taxableBase > 0 ? 'warning' : 'info', 'TAX_NOT_SET', null, 'Sales tax rate not set; tax is not included (it is not assumed to be 0)');
  } else {
    taxCents = applyPercent(taxableBase, config.tax_rate_pct);
  }
  if (shipping === null) {
    ctx.warn('info', 'SHIPPING_NOT_SET', null, 'Shipping/delivery not set; observed prices exclude shipping');
  }
  if (lines.length === 0 && labor.length === 0) {
    ctx.warn('warning', 'EMPTY_ESTIMATE', null, 'Estimate has no lines');
  }

  const costSubtotal = sumSafe([confirmed, provisional, allowance, laborCents, shipping ?? 0], 'cost subtotal');
  const pctBase = sumSafe([costSubtotal, taxCents ?? 0], 'percentage base');
  const markupCents = applyPercent(pctBase, config.markup_pct);
  const contingencyCents = applyPercent(pctBase, config.contingency_pct);
  const grandTotal = sumSafe([pctBase, markupCents, contingencyCents], 'grand total');

  const isComplete =
    missingLines === 0 &&
    provisionalLines === 0 &&
    missingLabor === 0 &&
    errors.length === 0 &&
    !(taxCents === null && taxableBase > 0) &&
    (lines.length > 0 || labor.length > 0);

  if (!isComplete && (config.markup_pct > 0 || config.contingency_pct > 0)) {
    ctx.warn('warning', 'MARKUP_ON_KNOWN_AMOUNTS_ONLY', null,
      'Markup/contingency applied only to known amounts; the estimate is incomplete');
  }

  const totals = {
    confirmed_cents: confirmed,
    provisional_cents: provisional,
    allowance_cents: allowance,
    labor_cents: laborCents,
    shipping_cents: shipping,
    cost_subtotal_known_cents: costSubtotal,
    line_count: lines.length,
    priced_lines: pricedLines,
    missing_lines: missingLines,
    provisional_lines: provisionalLines,
    allowance_lines: outLines.filter((l) => l.price_status === PRICE_STATUS.ALLOWANCE).length,
    by_owner_lines: byOwner.length,
    labor_items: labor.length,
    missing_labor_items: missingLabor,
    is_complete: isComplete,
    tax_rate_pct: config.tax_rate_pct,
    taxable_base_cents: taxableBase,
    tax_cents: taxCents,
    markup_pct: config.markup_pct,
    markup_cents: markupCents,
    contingency_pct: config.contingency_pct,
    contingency_cents: contingencyCents,
    percentage_base_cents: pctBase,
    markup_basis: isComplete ? 'complete' : 'known_amounts_only',
    grand_total_known_cents: grandTotal,
  };
  Object.assign(totals, buildDisplay(totals));

  return {
    module_version: MODULE_VERSION,
    as_of: asOfDay,
    lines: outLines,
    labor,
    totals,
    missing,
    by_owner: byOwner,
    warnings,
    errors,
  };
}
