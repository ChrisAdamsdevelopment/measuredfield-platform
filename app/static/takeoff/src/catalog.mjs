// MeasuredField Remodel Takeoff — product catalog and price observations (spec §3.6).
//
// A price observation is a dated, sourced fact ("this supplier item cost N cents
// on this date at this store"), never a live or confirmed job price. Freshness is
// always judged against an explicit as-of date supplied by the caller; this module
// never reads the clock.
//
// Shapes (see ARCHITECTURE_V2 §3.6):
//   Product        {id, material_key, trade, name, spec:{}, manufacturer|null, model|null,
//                   usable_unit, package:{label, coverage_qty, coverage_unit}|null,
//                   price_class, alternates:[]}
//   SupplierItem   {id, product_id, supplier_id, supplier_name, sku|null, url|null,
//                   match_confidence:0..1, match_method}
//   PriceObservation {id, supplier_item_id, price_cents:int, currency:'USD', per, store_id|null,
//                   zip|null, availability, observed_at, source_type, source_ref, source_license,
//                   reliability, tax_included:false, shipping_included:false}
// A product's package may be null when it is only ever priced per usable unit.

import { UnitError, convert, isKnownUnit, normalizeUnit, sameDimension } from './units.mjs';

export const MODULE_VERSION = '0.2.0';

/** Error thrown for malformed catalog records or missing inputs such as asOfDate. */
export class CatalogError extends Error {
  constructor(message) {
    super(message);
    this.name = 'CatalogError';
  }
}

export const PRICE_CLASSES = Object.freeze(['retail', 'supplier_quote', 'allowance', 'labor_only', 'by_owner']);
export const MATCH_METHODS = Object.freeze(['exact_model', 'manual', 'fuzzy_title']);
export const PRICE_PER = Object.freeze(['package', 'usable_unit']);
export const AVAILABILITY = Object.freeze(['in_stock', 'limited', 'out_of_stock', 'unknown']);
export const SOURCE_TYPES = Object.freeze([
  'authorized_feed',
  'retailer_api',
  'owner_import',
  'contractor_manual',
  'receipt',
  'licensed_third_party',
]);
export const RELIABILITY = Object.freeze(['receipt_verified', 'contractor_entered', 'imported_unverified']);
export const SUPPORTED_CURRENCIES = Object.freeze(['USD']);

/**
 * Age limits in whole calendar days per source_type. An observation is 'fresh'
 * through `fresh` days old (inclusive), 'stale' through `stale` days, then 'expired'.
 * Retail-derived sources (feeds, APIs, owner imports of retail data, licensed
 * third-party data) age fastest; contractor-entered prices and receipts last longer.
 */
export const FRESHNESS_POLICY = Object.freeze({
  authorized_feed: Object.freeze({ fresh: 14, stale: 45 }),
  retailer_api: Object.freeze({ fresh: 14, stale: 45 }),
  owner_import: Object.freeze({ fresh: 14, stale: 45 }),
  licensed_third_party: Object.freeze({ fresh: 14, stale: 45 }),
  contractor_manual: Object.freeze({ fresh: 30, stale: 90 }),
  receipt: Object.freeze({ fresh: 30, stale: 90 }),
});

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

const MS_PER_DAY = 86400000;
const ISO_DATE_RE =
  /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

/**
 * Calendar day number (days since 1970-01-01) of a date value.
 * Accepts 'YYYY-MM-DD', an ISO 8601 date-time, or a valid Date (its UTC date).
 * For strings the calendar date as written is used, so results never depend on the
 * machine's time zone. Throws CatalogError on anything else, including impossible
 * dates such as 2026-02-30.
 */
export function toCalendarDay(value) {
  if (value instanceof Date) {
    const ms = value.getTime();
    if (!Number.isFinite(ms)) throw new CatalogError('Invalid Date');
    return Math.floor(ms / MS_PER_DAY);
  }
  if (typeof value !== 'string') {
    throw new CatalogError(`Expected an ISO date string, got ${value === null ? 'null' : typeof value}`);
  }
  const match = ISO_DATE_RE.exec(value.trim());
  if (!match) throw new CatalogError(`"${value}" is not an ISO date (YYYY-MM-DD)`);
  const [, y, mo, d, hh, mi, ss] = match;
  const year = Number(y);
  const month = Number(mo);
  const day = Number(d);
  const ms = Date.UTC(year, month - 1, day);
  const check = new Date(ms);
  const validDate =
    check.getUTCFullYear() === year && check.getUTCMonth() === month - 1 && check.getUTCDate() === day;
  const validTime =
    hh === undefined || (Number(hh) < 24 && Number(mi) < 60 && (ss === undefined || Number(ss) < 60));
  if (!validDate || !validTime) throw new CatalogError(`"${value}" is not a valid calendar date`);
  return ms / MS_PER_DAY;
}

/** True when `value` is an accepted ISO date / date-time / valid Date; never throws. */
export function isValidIsoDate(value) {
  try {
    toCalendarDay(value);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Freshness
// ---------------------------------------------------------------------------

/**
 * Freshness of a price observation with its reasoning:
 * `{status, age_days, fresh_days, stale_days, reason}`.
 * `asOfDate` is REQUIRED — there is no implicit "now". An observation dated after
 * asOfDate is not valid as of that date and is reported 'expired' with reason
 * 'observed_after_as_of' so it can never be priced.
 */
export function priceFreshnessDetail(obs, asOfDate) {
  if (asOfDate === undefined || asOfDate === null || asOfDate === '') {
    throw new CatalogError('asOfDate is required to judge price freshness (no implicit current date)');
  }
  const asOfDay = toCalendarDay(asOfDate);
  if (!obs || typeof obs !== 'object') throw new CatalogError('Price observation is required');
  const policy = FRESHNESS_POLICY[obs.source_type];
  if (!policy) throw new CatalogError(`No freshness policy for source_type "${obs.source_type}"`);
  if (obs.observed_at === undefined || obs.observed_at === null || obs.observed_at === '') {
    throw new CatalogError(`Observation ${obs.id ?? '(no id)'} has no observed_at date`);
  }
  const ageDays = asOfDay - toCalendarDay(obs.observed_at);
  const base = { age_days: ageDays, fresh_days: policy.fresh, stale_days: policy.stale };
  if (ageDays < 0) return { status: 'expired', ...base, reason: 'observed_after_as_of' };
  if (ageDays <= policy.fresh) return { status: 'fresh', ...base, reason: 'within_fresh_window' };
  if (ageDays <= policy.stale) return { status: 'stale', ...base, reason: 'past_fresh_window' };
  return { status: 'expired', ...base, reason: 'past_stale_window' };
}

/** 'fresh' | 'stale' | 'expired' for an observation as of `asOfDate` (required). */
export function priceFreshness(obs, asOfDate) {
  return priceFreshnessDetail(obs, asOfDate).status;
}

// ---------------------------------------------------------------------------
// Unit cost
// ---------------------------------------------------------------------------

function isPositiveCents(value) {
  return Number.isSafeInteger(value) && value > 0;
}

/**
 * Cost per usable unit of a product from one observation:
 * `{cents_per_usable_unit (float), usable_unit}`.
 * per 'package' divides by the package coverage converted to the usable unit
 * (e.g. 1234 cents per 10.76 sf box -> ~114.68 cents/sf). Throws on non-positive or
 * non-integer cents, non-USD currency, missing package, or unit dimension mismatch.
 */
export function normalizedUnitCost(obs, product) {
  if (!obs || typeof obs !== 'object') throw new CatalogError('Price observation is required');
  if (!product || typeof product !== 'object') throw new CatalogError('Product is required');
  if (!isPositiveCents(obs.price_cents)) {
    throw new CatalogError(`Observation ${obs.id ?? ''} price_cents must be a positive integer, got ${obs.price_cents}`);
  }
  if (!SUPPORTED_CURRENCIES.includes(obs.currency)) {
    throw new CatalogError(`Unsupported currency "${obs.currency}" (only USD; no currency conversion is performed)`);
  }
  const usableUnit = normalizeUnit(product.usable_unit);

  if (obs.per === 'usable_unit') {
    return { cents_per_usable_unit: obs.price_cents, usable_unit: usableUnit };
  }
  if (obs.per !== 'package') {
    throw new CatalogError(`Observation per must be "package" or "usable_unit", got "${obs.per}"`);
  }
  const pkg = product.package;
  if (!pkg) throw new CatalogError(`Product ${product.id} has no package, so a per-package price cannot be normalised`);
  if (typeof pkg.coverage_qty !== 'number' || !Number.isFinite(pkg.coverage_qty) || pkg.coverage_qty <= 0) {
    throw new CatalogError(`Product ${product.id} package coverage_qty must be greater than 0`);
  }
  const coverage = convert(pkg.coverage_qty, pkg.coverage_unit, usableUnit); // UnitError on mismatch
  return { cents_per_usable_unit: obs.price_cents / coverage, usable_unit: usableUnit };
}

// ---------------------------------------------------------------------------
// Lookup helpers
// ---------------------------------------------------------------------------

/** An empty catalog `{products:[], supplierItems:[], observations:[]}`. */
export function emptyCatalog() {
  return { products: [], supplierItems: [], observations: [] };
}

/** Maps by id: `{products, supplierItems, observations}` (later duplicates do not replace earlier ones). */
export function indexCatalog(catalog = {}) {
  const byId = (list) => {
    const map = new Map();
    for (const item of Array.isArray(list) ? list : []) {
      if (item && item.id !== undefined && !map.has(item.id)) map.set(item.id, item);
    }
    return map;
  };
  return {
    products: byId(catalog.products),
    supplierItems: byId(catalog.supplierItems),
    observations: byId(catalog.observations),
  };
}

/**
 * Resolve an observation id to `{observation, supplier_item, product}`.
 * Throws CatalogError when the id or any link in the chain is missing.
 */
export function resolveObservation(catalog, observationId) {
  const index = indexCatalog(catalog);
  const observation = index.observations.get(observationId);
  if (!observation) throw new CatalogError(`Unknown price observation "${observationId}"`);
  const supplierItem = index.supplierItems.get(observation.supplier_item_id);
  if (!supplierItem) {
    throw new CatalogError(`Observation "${observationId}" references unknown supplier item "${observation.supplier_item_id}"`);
  }
  const product = index.products.get(supplierItem.product_id);
  if (!product) {
    throw new CatalogError(`Supplier item "${supplierItem.id}" references unknown product "${supplierItem.product_id}"`);
  }
  return { observation, supplier_item: supplierItem, product };
}

/** All `{observation, supplier_item}` pairs for a product, in catalog order. */
export function observationsForProduct(catalog, productId) {
  const items = (catalog?.supplierItems ?? []).filter((si) => si.product_id === productId);
  const itemsById = new Map(items.map((si) => [si.id, si]));
  return (catalog?.observations ?? [])
    .filter((obs) => itemsById.has(obs.supplier_item_id))
    .map((obs) => ({ observation: obs, supplier_item: itemsById.get(obs.supplier_item_id) }));
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stableJson(value[k])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/**
 * Merge catalogs (e.g. an existing project catalog plus a new CSV import) by record id.
 * Returns `{catalog, conflicts}`. Identical records are kept once; when two records share
 * an id but differ, the earlier one is kept and the clash is reported in `conflicts`
 * as `{entity, id}` — nothing is silently overwritten. Inputs are not modified.
 */
export function mergeCatalogs(...catalogs) {
  const merged = emptyCatalog();
  const conflicts = [];
  const entities = { products: 'product', supplierItems: 'supplier_item', observations: 'observation' };
  for (const [key, entity] of Object.entries(entities)) {
    const seen = new Map();
    for (const catalog of catalogs) {
      for (const record of Array.isArray(catalog?.[key]) ? catalog[key] : []) {
        if (!record || typeof record !== 'object') continue;
        const existing = seen.get(record.id);
        if (existing === undefined) {
          seen.set(record.id, stableJson(record));
          merged[key].push(record);
        } else if (existing !== stableJson(record)) {
          conflicts.push({ entity, id: record.id });
        }
      }
    }
  }
  return { catalog: merged, conflicts };
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const isNonEmptyString = (v) => typeof v === 'string' && v.trim() !== '';

/**
 * Validate a catalog `{products, supplierItems, observations}` -> `{errors, warnings}`.
 * Each issue is `{code, entity, id, message}`. Errors cover dangling ids, duplicate ids,
 * unknown units, package/usable unit dimension mismatch, zero/negative/non-integer
 * prices, invalid enum values and duplicate (supplier_item, store, observed_at)
 * observations. Pass `{asOfDate}` to also warn about stale, expired or future-dated prices.
 */
export function validateCatalog(catalog = {}, { asOfDate } = {}) {
  const errors = [];
  const warnings = [];
  const error = (code, entity, id, message) => errors.push({ code, entity, id: id ?? null, message });
  const warn = (code, entity, id, message) => warnings.push({ code, entity, id: id ?? null, message });

  const lists = {};
  for (const key of ['products', 'supplierItems', 'observations']) {
    const value = catalog?.[key];
    if (value === undefined || value === null) lists[key] = [];
    else if (!Array.isArray(value)) {
      error('not_an_array', key, null, `${key} must be an array`);
      lists[key] = [];
    } else lists[key] = value;
  }

  const productsById = checkIds(lists.products, 'product', error);
  const itemsById = checkIds(lists.supplierItems, 'supplier_item', error);
  checkIds(lists.observations, 'observation', error);

  for (const product of lists.products) validateProduct(product, productsById, error, warn);
  for (const item of lists.supplierItems) validateSupplierItem(item, productsById, error, warn);

  const seenObservationKeys = new Map();
  for (const obs of lists.observations) {
    validateObservation(obs, { itemsById, productsById, seenObservationKeys, asOfDate }, error, warn);
  }
  return { errors, warnings };
}

function checkIds(list, entity, error) {
  const byId = new Map();
  for (const record of list) {
    if (!record || typeof record !== 'object') {
      error('invalid_record', entity, null, `${entity} entry is not an object`);
      continue;
    }
    if (!isNonEmptyString(record.id)) {
      error('missing_id', entity, null, `${entity} is missing an id`);
      continue;
    }
    if (byId.has(record.id)) error('duplicate_id', entity, record.id, `Duplicate ${entity} id "${record.id}"`);
    else byId.set(record.id, record);
  }
  return byId;
}

function validateProduct(product, productsById, error, warn) {
  if (!product || typeof product !== 'object') return;
  const id = product.id;
  if (!isNonEmptyString(product.material_key)) error('missing_material_key', 'product', id, 'Product has no material_key');
  if (!isNonEmptyString(product.name)) error('missing_name', 'product', id, 'Product has no name');
  if (!PRICE_CLASSES.includes(product.price_class)) {
    error('invalid_price_class', 'product', id, `Invalid price_class "${product.price_class}"`);
  }

  const usableOk = isKnownUnit(product.usable_unit);
  if (!usableOk) error('unknown_unit', 'product', id, `Unknown usable_unit "${product.usable_unit}"`);

  const pkg = product.package;
  if (pkg !== null && pkg !== undefined) {
    if (typeof pkg.coverage_qty !== 'number' || !Number.isFinite(pkg.coverage_qty) || pkg.coverage_qty <= 0) {
      error('invalid_package_coverage', 'product', id, 'Package coverage_qty must be a number greater than 0');
    }
    if (!isKnownUnit(pkg.coverage_unit)) {
      error('unknown_unit', 'product', id, `Unknown package coverage_unit "${pkg.coverage_unit}"`);
    } else if (usableOk && !sameDimension(pkg.coverage_unit, product.usable_unit)) {
      error(
        'unit_dimension_mismatch',
        'product',
        id,
        `Package coverage unit "${pkg.coverage_unit}" is not the same dimension as usable_unit "${product.usable_unit}"`,
      );
    }
    if (!isNonEmptyString(pkg.label)) warn('missing_package_label', 'product', id, 'Package has no label');
  }

  for (const altId of Array.isArray(product.alternates) ? product.alternates : []) {
    if (!productsById.has(altId)) warn('dangling_alternate', 'product', id, `Alternate "${altId}" is not in the catalog`);
  }
}

function validateSupplierItem(item, productsById, error, warn) {
  if (!item || typeof item !== 'object') return;
  const id = item.id;
  if (!productsById.has(item.product_id)) {
    error('dangling_product_id', 'supplier_item', id, `Supplier item references unknown product "${item.product_id}"`);
  }
  if (!isNonEmptyString(item.supplier_name)) warn('missing_supplier_name', 'supplier_item', id, 'Supplier item has no supplier_name');
  const conf = item.match_confidence;
  if (typeof conf !== 'number' || !Number.isFinite(conf) || conf < 0 || conf > 1) {
    error('invalid_match_confidence', 'supplier_item', id, 'match_confidence must be a number from 0 to 1');
  }
  if (!MATCH_METHODS.includes(item.match_method)) {
    error('invalid_match_method', 'supplier_item', id, `Invalid match_method "${item.match_method}"`);
  } else if (item.match_method === 'fuzzy_title') {
    warn('fuzzy_match', 'supplier_item', id, 'Fuzzy title match — confirm the product before relying on this price');
  }
}

function validateObservation(obs, ctx, error, warn) {
  if (!obs || typeof obs !== 'object') return;
  const id = obs.id;
  const item = ctx.itemsById.get(obs.supplier_item_id);
  if (!item) {
    error('dangling_supplier_item_id', 'observation', id, `Observation references unknown supplier item "${obs.supplier_item_id}"`);
  }

  if (!Number.isInteger(obs.price_cents)) {
    error('price_not_integer_cents', 'observation', id, `price_cents must be an integer, got ${obs.price_cents}`);
  } else if (obs.price_cents <= 0) {
    error('non_positive_price', 'observation', id, `price_cents must be greater than 0, got ${obs.price_cents}`);
  } else if (!Number.isSafeInteger(obs.price_cents)) {
    error('price_out_of_range', 'observation', id, 'price_cents is outside the safe integer range');
  }

  if (!SUPPORTED_CURRENCIES.includes(obs.currency)) error('unsupported_currency', 'observation', id, `Unsupported currency "${obs.currency}"`);
  if (!PRICE_PER.includes(obs.per)) error('invalid_per', 'observation', id, `Invalid per "${obs.per}"`);
  if (!AVAILABILITY.includes(obs.availability)) error('invalid_availability', 'observation', id, `Invalid availability "${obs.availability}"`);
  if (!SOURCE_TYPES.includes(obs.source_type)) error('invalid_source_type', 'observation', id, `Invalid source_type "${obs.source_type}"`);
  if (!RELIABILITY.includes(obs.reliability)) error('invalid_reliability', 'observation', id, `Invalid reliability "${obs.reliability}"`);

  const dateOk = isValidIsoDate(obs.observed_at);
  if (!dateOk) error('invalid_observed_at', 'observation', id, `observed_at "${obs.observed_at}" is not an ISO date`);

  if (!isNonEmptyString(obs.source_ref)) warn('missing_source_ref', 'observation', id, 'Observation has no source_ref');
  if (!isNonEmptyString(obs.source_license)) warn('missing_source_license', 'observation', id, 'Observation has no source_license recorded');
  if (obs.tax_included === true) warn('tax_included', 'observation', id, 'Price includes tax; estimates add tax separately');
  if (obs.shipping_included === true) warn('shipping_included', 'observation', id, 'Price includes shipping; estimates add shipping separately');

  // A per-package price needs a usable package on the linked product.
  const product = item ? ctx.productsById.get(item.product_id) : undefined;
  if (product && obs.per === 'package') {
    try {
      if (Number.isInteger(obs.price_cents) && obs.price_cents > 0 && obs.currency === 'USD') normalizedUnitCost(obs, product);
      else if (!product.package) throw new CatalogError('no package');
    } catch (e) {
      if (e instanceof UnitError || e instanceof CatalogError) {
        error('cannot_normalize_price', 'observation', id, `Per-package price cannot be normalised for product "${product.id}": ${e.message}`);
      } else throw e;
    }
  }

  // Duplicate (supplier_item, store, observed_at) leaves the applicable price ambiguous.
  if (dateOk && obs.supplier_item_id !== undefined) {
    const key = `${obs.supplier_item_id}|${obs.store_id ?? ''}|${String(obs.observed_at).trim()}`;
    const first = ctx.seenObservationKeys.get(key);
    if (first) {
      const samePrice = first.price_cents === obs.price_cents;
      error(
        'duplicate_observation',
        'observation',
        id,
        `Duplicate of observation "${first.id}" (same supplier item, store and observed_at${samePrice ? '' : ', different price'})`,
      );
    } else ctx.seenObservationKeys.set(key, obs);
  }

  if (ctx.asOfDate !== undefined && ctx.asOfDate !== null && dateOk && FRESHNESS_POLICY[obs.source_type]) {
    const detail = priceFreshnessDetail(obs, ctx.asOfDate);
    if (detail.reason === 'observed_after_as_of') {
      warn('observed_after_as_of', 'observation', id, `observed_at is after the as-of date (${detail.age_days} days)`);
    } else if (detail.status !== 'fresh') {
      warn(`price_${detail.status}`, 'observation', id, `Price is ${detail.status}: ${detail.age_days} days old`);
    }
  }
}
