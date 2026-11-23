// MeasuredField Remodel Takeoff — CSV input/output (spec §3.7).
//
// * parseCsv: RFC 4180 reader (quoted fields, escaped quotes, CRLF/LF/CR, BOM)
//   with hard limits so a pasted or dropped file cannot exhaust the browser.
// * importCatalogCsv: turns a material catalog CSV (template columns below) into
//   catalog records {products, supplierItems, observations} plus a row-level
//   report. Rows are accepted or rejected as a whole. An empty price never
//   becomes $0: the product is imported without a price observation and a
//   warning says so. Nothing is invented — no default dates, no currency
//   conversion, no rounding of over-precise prices.
// * toCsv / exportBomCsv: CSV writer that always quotes, uses CRLF and
//   neutralises spreadsheet formula injection.
//
// Pure logic: no DOM, no clock, no network, no dependencies outside src/.

import { UnitError, isKnownUnit, normalizeUnit, roundQty, sameDimension, UNITS } from './units.mjs';
import {
  AVAILABILITY,
  PRICE_CLASSES,
  SOURCE_TYPES,
  isValidIsoDate,
  priceFreshnessDetail,
} from './catalog.mjs';

export const MODULE_VERSION = '0.2.0';

/** Error thrown for malformed CSV text, exceeded limits and invalid import options. */
export class CsvError extends Error {
  constructor(message, code = 'CSV_ERROR', line = null) {
    super(message);
    this.name = 'CsvError';
    this.code = code;
    this.line = line;
  }
}

/** Hard input limits: 50,000 records (header included) and 5 MB of UTF-8 text. */
export const CSV_LIMITS = Object.freeze({
  max_rows: 50000,
  max_bytes: 5 * 1024 * 1024,
  max_field_chars: 2000,
});

/** Column order of the material catalog template (public/samples/material-catalog-template.csv). */
export const CATALOG_TEMPLATE_COLUMNS = Object.freeze([
  'material_key',
  'trade',
  'product_name',
  'manufacturer',
  'model',
  'supplier',
  'sku',
  'url',
  'store_id',
  'zip',
  'package_label',
  'package_coverage_qty',
  'package_coverage_unit',
  'usable_unit',
  'price',
  'price_per',
  'currency',
  'availability',
  'observed_at',
  'source_type',
  'source_license',
  'notes',
]);

/** Columns every catalog CSV must have (after mapping). */
export const CATALOG_REQUIRED_COLUMNS = Object.freeze(['material_key', 'product_name', 'usable_unit']);

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

function utf8ByteLength(text) {
  let bytes = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      const next = text.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        bytes += 4; // surrogate pair
        i++;
      } else bytes += 3;
    } else bytes += 3;
  }
  return bytes;
}

function countLineBreaks(text) {
  const matches = text.match(/\r\n|\r|\n/g);
  return matches ? matches.length : 0;
}

/**
 * Parse CSV into records with their starting line numbers: `[{fields, line}]`.
 * Completely empty lines are skipped. Throws CsvError on limits or malformed quoting.
 */
function parseCsvRecords(text, { maxRows = CSV_LIMITS.max_rows, maxBytes = CSV_LIMITS.max_bytes } = {}) {
  if (typeof text !== 'string') {
    throw new CsvError(`CSV input must be text, got ${text === null ? 'null' : typeof text}`, 'INVALID_INPUT');
  }
  // A UTF-16 code unit is at least one UTF-8 byte, so the cheap check rules most text in or out.
  if (text.length > maxBytes || (text.length * 3 > maxBytes && utf8ByteLength(text) > maxBytes)) {
    throw new CsvError(`CSV is larger than the ${maxBytes} byte limit`, 'TOO_LARGE');
  }

  const n = text.length;
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0; // byte-order mark
  let line = 1;
  const records = [];

  while (i < n) {
    const startLine = line;
    const startIndex = i;
    const fields = [];

    for (;;) {
      let value;
      if (text[i] === '"') {
        // Quoted field: runs to the next unpaired quote; "" is an escaped quote.
        const quoteLine = line;
        let j = i + 1;
        let buf = '';
        let closed = false;
        while (j < n) {
          const q = text.indexOf('"', j);
          if (q === -1) break;
          buf += text.slice(j, q);
          if (text[q + 1] === '"') {
            buf += '"';
            j = q + 2;
          } else {
            j = q + 1;
            closed = true;
            break;
          }
        }
        if (!closed) {
          throw new CsvError(`Unterminated quoted field starting on line ${quoteLine}`, 'UNTERMINATED_QUOTE', quoteLine);
        }
        line += countLineBreaks(buf);
        i = j;
        if (i < n && text[i] !== ',' && text[i] !== '\n' && text[i] !== '\r') {
          throw new CsvError(`Unexpected text after a closing quote on line ${line}`, 'MALFORMED_QUOTE', line);
        }
        value = buf;
      } else {
        // Unquoted field: runs to the next comma or line break. A stray quote inside
        // (e.g. the inch mark in 12" tile) is kept literally.
        let j = i;
        while (j < n) {
          const c = text.charCodeAt(j);
          if (c === 44 /* , */ || c === 10 /* \n */ || c === 13 /* \r */) break;
          j++;
        }
        value = text.slice(i, j);
        i = j;
      }
      fields.push(value);

      if (i >= n) break;
      if (text[i] === ',') {
        i++;
        if (i >= n) {
          fields.push(''); // trailing comma at end of input
          break;
        }
        continue;
      }
      // Line break: CRLF, LF or a lone CR ends the record.
      if (text[i] === '\r' && text[i + 1] === '\n') i += 2;
      else i += 1;
      line++;
      break;
    }

    const emptyLine = fields.length === 1 && fields[0] === '' && text[startIndex] !== '"';
    if (!emptyLine) {
      records.push({ fields, line: startLine });
      if (records.length > maxRows) {
        throw new CsvError(`CSV has more than ${maxRows} rows`, 'TOO_MANY_ROWS', startLine);
      }
    }
  }
  return records;
}

/**
 * Parse RFC 4180 CSV text into rows (arrays of strings).
 * Handles quoted fields, escaped quotes (""), embedded line breaks, CRLF/LF/CR line
 * endings and a leading BOM; completely empty lines are skipped. A quote inside an
 * unquoted field is kept literally. Throws CsvError for non-string input, unterminated
 * or malformed quotes, more than `maxRows` rows (default 50,000, header included) or more
 * than `maxBytes` of UTF-8 (default 5 MB).
 */
export function parseCsv(text, options = {}) {
  return parseCsvRecords(text, options).map((record) => record.fields);
}

// ---------------------------------------------------------------------------
// Value parsing
// ---------------------------------------------------------------------------

const PRICE_RE = /^(\d{1,3}(?:,\d{3})+|\d+)?(?:\.(\d+))?$/;

/**
 * Parse a US-dollar price into integer cents.
 * '$1,234.56' -> 123456, '1234.56' -> 123456, '12' -> 1200, '' -> null (no price; never 0).
 * Throws CsvError with code INVALID_PRICE (e.g. 'abc', '1.234,56', '1e3', '1/2'),
 * NEGATIVE_PRICE ('-5', '$-5', '(5.00)'), TOO_PRECISE (more than 2 decimals; a price is
 * never rounded into existence) or PRICE_OUT_OF_RANGE. Zero parses as 0; callers decide
 * whether zero is acceptable (the catalog import rejects it).
 */
export function parsePriceCents(text) {
  if (text === null || text === undefined) return null;
  if (typeof text === 'number') {
    throw new CsvError('Price must be text such as "12.34"; numbers are ambiguous about cents', 'INVALID_PRICE');
  }
  if (typeof text !== 'string') throw new CsvError(`Price must be text, got ${typeof text}`, 'INVALID_PRICE');
  let s = text.normalize('NFKC').trim();
  if (s === '') return null;

  if (/^[-−(]|^(?:usd|\$)\s*[-−]/i.test(s)) {
    throw new CsvError(`Negative price "${text}" is not allowed`, 'NEGATIVE_PRICE');
  }
  s = s
    .replace(/^usd\s*/i, '')
    .replace(/\s*usd$/i, '')
    .replace(/^\$\s*/, '')
    .trim();
  const match = PRICE_RE.exec(s);
  if (!match || (match[1] === undefined && match[2] === undefined)) {
    throw new CsvError(`"${text}" is not a price (expected e.g. 1234.56 or $1,234.56)`, 'INVALID_PRICE');
  }
  const whole = (match[1] ?? '0').replace(/,/g, '');
  const fraction = match[2] ?? '';
  if (fraction.length > 2) {
    throw new CsvError(`Price "${text}" has more than 2 decimal places`, 'TOO_PRECISE');
  }
  if (whole.replace(/^0+/, '').length > 13) {
    throw new CsvError(`Price "${text}" is out of range`, 'PRICE_OUT_OF_RANGE');
  }
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(cents)) throw new CsvError(`Price "${text}" is out of range`, 'PRICE_OUT_OF_RANGE');
  return cents;
}

const QTY_RE = /^(\d{1,3}(?:,\d{3})+|\d+)?(?:\.(\d+))?$/;

/**
 * Parse a plain positive-or-zero decimal quantity ('10.76', '1,000', '.5').
 * '' -> null. Throws CsvError (INVALID_NUMBER) for negatives, fractions, exponents or text.
 */
export function parseQuantity(text) {
  if (text === null || text === undefined) return null;
  const s = String(text).normalize('NFKC').trim();
  if (s === '') return null;
  const match = QTY_RE.exec(s);
  if (!match || (match[1] === undefined && match[2] === undefined)) {
    throw new CsvError(`"${text}" is not a number (expected e.g. 10.76)`, 'INVALID_NUMBER');
  }
  const value = Number(s.replace(/,/g, ''));
  if (!Number.isFinite(value)) throw new CsvError(`"${text}" is not a finite number`, 'INVALID_NUMBER');
  return value;
}

// ---------------------------------------------------------------------------
// Catalog import
// ---------------------------------------------------------------------------

function normalizeHeader(text) {
  return String(text ?? '')
    .normalize('NFKC')
    .replace(/^﻿/, '')
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_');
}

// Control characters (including line breaks inside quoted cells) become spaces.
function cleanCell(value) {
  return String(value ?? '')
    .replace(/[\u0000-\u001F\u007F]+/g, ' ')
    .replace(/ {2,}/g, ' ')
    .trim();
}

/** 32-bit FNV-1a hash as 8 hex characters (deterministic record ids). */
function fnv1a(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

function slugify(text) {
  return text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '') // strip combining accents
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
}

/** Allocates deterministic ids; a hash collision between different keys gets a suffix. */
function idAllocator() {
  const keysById = new Map();
  return (prefix, key) => {
    const base = `${prefix}_${fnv1a(key)}`;
    let id = base;
    for (let n = 2; keysById.has(id) && keysById.get(id) !== key; n++) id = `${base}_${n}`;
    keysById.set(id, key);
    return id;
  };
}

function normalizeEnum(text) {
  return text.toLowerCase().replace(/[\s-]+/g, '_');
}

function safeUrl(text) {
  try {
    const url = new URL(text);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

function resolvePricePer(text, usableUnit, pkg) {
  const t = normalizeEnum(text);
  if (['package', 'pkg', 'per_package'].includes(t)) return 'package';
  if (['usable_unit', 'unit', 'per_unit', 'per_usable_unit'].includes(t)) return 'usable_unit';
  if (pkg && t === normalizeEnum(pkg.label)) return 'package';
  if (isKnownUnit(text) && normalizeUnit(text) === usableUnit) return 'usable_unit';
  return null;
}

function reliabilityFor(sourceType) {
  // An import cannot verify anything; only contractor-entered data is marked as such.
  return sourceType === 'contractor_manual' ? 'contractor_entered' : 'imported_unverified';
}

class RowRejection extends Error {
  constructor(code, reason) {
    super(reason);
    this.code = code;
  }
}

function reject(code, reason) {
  throw new RowRejection(code, reason);
}

function resolveColumns(headerFields, mapping) {
  if (mapping !== undefined && mapping !== null && (typeof mapping !== 'object' || Array.isArray(mapping))) {
    throw new CsvError('mapping must be an object {template_column: "Header in file"}', 'INVALID_MAPPING');
  }
  const headers = headerFields.map(normalizeHeader);
  const positions = new Map();
  headers.forEach((h, index) => {
    if (h === '') return;
    if (!positions.has(h)) positions.set(h, []);
    positions.get(h).push(index);
  });

  const columns = {};
  const used = new Set();
  for (const [target, source] of Object.entries(mapping ?? {})) {
    if (!CATALOG_TEMPLATE_COLUMNS.includes(target)) {
      throw new CsvError(`mapping target "${target}" is not a template column`, 'UNKNOWN_MAPPING_TARGET');
    }
    if (source === null || source === undefined || source === '') continue;
    const found = positions.get(normalizeHeader(source));
    if (!found) throw new CsvError(`Mapped column "${source}" (for ${target}) is not in the CSV header`, 'MAPPED_COLUMN_NOT_FOUND');
    if (found.length > 1) throw new CsvError(`Column "${source}" appears more than once in the header`, 'DUPLICATE_COLUMN');
    columns[target] = found[0];
    used.add(found[0]);
  }
  for (const target of CATALOG_TEMPLATE_COLUMNS) {
    // A mapping entry of null/'' deliberately leaves that template column unread.
    if (columns[target] !== undefined || (mapping && Object.prototype.hasOwnProperty.call(mapping, target))) continue;
    const found = positions.get(target);
    if (!found) continue;
    if (found.length > 1) throw new CsvError(`Column "${target}" appears more than once in the header`, 'DUPLICATE_COLUMN');
    if (used.has(found[0])) continue; // already mapped to another template column
    columns[target] = found[0];
    used.add(found[0]);
  }

  const missing = CATALOG_REQUIRED_COLUMNS.filter((c) => columns[c] === undefined);
  if (missing.length > 0) {
    throw new CsvError(`CSV is missing required column(s): ${missing.join(', ')}`, 'MISSING_COLUMNS');
  }
  const ignored = headerFields.filter((h, index) => normalizeHeader(h) !== '' && !used.has(index));
  return { columns, width: headerFields.length, ignored };
}

/**
 * Import a material catalog CSV.
 *
 * @param {string} text CSV text with a header row (template columns, see CATALOG_TEMPLATE_COLUMNS).
 * @param {object} [options]
 * @param {object} [options.mapping] `{template_column: 'Header in your file'}` for non-template headers.
 * @param {string} [options.source_ref] where the file came from (e.g. its name); each observation
 *        gets `${source_ref}#row=N`.
 * @param {string} [options.source_license] default licence/terms note when a row has none.
 * @param {string} [options.imported_at] ISO date/time of the import (no implicit now). When given,
 *        prices that are stale/expired/future-dated relative to it are flagged.
 * @param {string} [options.price_class] price_class for imported products (default 'retail').
 * @returns {{products, supplierItems, observations, report:{rows, imported, rejected:[{row, code, reason}],
 *          warnings:[{row, code, message}], products_created, supplier_items_created,
 *          observations_created, unpriced_rows, ignored_columns}}}
 * Rows are accepted or rejected whole; `row` is the 1-based line number in the file.
 * Throws CsvError for unreadable CSV, missing required columns or invalid options.
 */
export function importCatalogCsv(text, options = {}) {
  const { mapping, source_ref: sourceRef, source_license: defaultLicense, imported_at: importedAt } = options ?? {};
  const priceClass = options?.price_class ?? 'retail';
  if (!PRICE_CLASSES.includes(priceClass)) {
    throw new CsvError(`price_class "${priceClass}" is not one of ${PRICE_CLASSES.join(', ')}`, 'INVALID_OPTION');
  }
  if (importedAt !== undefined && importedAt !== null && !isValidIsoDate(importedAt)) {
    throw new CsvError(`imported_at "${importedAt}" is not an ISO date`, 'INVALID_OPTION');
  }

  const records = parseCsvRecords(text);
  if (records.length === 0) throw new CsvError('CSV is empty (a header row is required)', 'EMPTY_CSV');
  const [headerRecord, ...dataRecords] = records;
  const { columns, width, ignored } = resolveColumns(headerRecord.fields, mapping);

  const products = [];
  const supplierItems = [];
  const observations = [];
  const rejected = [];
  const warnings = [];
  const warn = (row, code, message) => warnings.push({ row, code, message });

  if (ignored.length > 0) warn(null, 'ignored_columns', `Column(s) not used by the importer: ${ignored.join(', ')}`);
  if (!sourceRef) warn(null, 'source_ref_not_set', 'No source_ref given; observations reference "csv-import"');
  if (!importedAt) warn(null, 'imported_at_not_set', 'No imported_at date given; price freshness was not checked at import');

  const allocate = idAllocator();
  const productIndex = new Map(); // product key -> product
  const itemIndex = new Map(); // supplier item key -> supplier item
  const observationIndex = new Map(); // (supplier item, store, observed_at) -> row
  const baseRef = sourceRef ? String(sourceRef) : 'csv-import';
  let rows = 0;
  let unpricedRows = 0;

  for (const record of dataRecords) {
    const raw = record.fields;
    const get = (column) => (columns[column] === undefined ? '' : cleanCell(raw[columns[column]]));
    const allEmpty = raw.every((f) => cleanCell(f) === '');
    if (allEmpty) continue;
    rows++;

    const rowWarnings = [];
    const rowWarn = (code, message) => rowWarnings.push({ row: record.line, code, message });
    try {
      if (raw.length > width && raw.slice(width).some((f) => cleanCell(f) !== '')) {
        reject('too_many_fields', `Row has ${raw.length} fields but the header has ${width}`);
      }
      for (const column of Object.keys(columns)) {
        if (get(column).length > CSV_LIMITS.max_field_chars) {
          reject('field_too_long', `${column} is longer than ${CSV_LIMITS.max_field_chars} characters`);
        }
      }

      // Product -------------------------------------------------------------
      const materialKey = get('material_key').toLowerCase();
      if (materialKey === '') reject('missing_material_key', 'material_key is empty');
      if (!/^[a-z0-9][a-z0-9_.:-]*$/.test(materialKey)) {
        rowWarn('material_key_nonstandard', `material_key "${materialKey}" is not snake_case; it may not match takeoff lines`);
      }
      const name = get('product_name');
      if (name === '') reject('missing_product_name', 'product_name is empty');
      if (/^EXAMPLE\b/.test(name)) {
        rowWarn('template_example_row', 'This looks like a template EXAMPLE row; delete it unless it is your own data');
      }

      const usableText = get('usable_unit');
      if (usableText === '') reject('missing_usable_unit', 'usable_unit is empty');
      let usableUnit;
      try {
        usableUnit = normalizeUnit(usableText);
      } catch (e) {
        if (!(e instanceof UnitError)) throw e;
        reject('unknown_unit', `usable_unit "${usableText}" is not a unit (packages such as box or sheet go in package_label)`);
      }

      const pkgLabel = get('package_label');
      const pkgQtyText = get('package_coverage_qty');
      const pkgUnitText = get('package_coverage_unit');
      let pkg = null;
      if (pkgLabel !== '' || pkgQtyText !== '' || pkgUnitText !== '') {
        if (pkgQtyText === '' || pkgUnitText === '') {
          reject('incomplete_package', 'package_coverage_qty and package_coverage_unit are both required when a package is described');
        }
        let coverageQty;
        try {
          coverageQty = parseQuantity(pkgQtyText);
        } catch {
          reject('invalid_package_coverage', `package_coverage_qty "${pkgQtyText}" is not a number`);
        }
        if (!(coverageQty > 0)) reject('invalid_package_coverage', 'package_coverage_qty must be greater than 0');
        let coverageUnit;
        try {
          coverageUnit = normalizeUnit(pkgUnitText);
        } catch (e) {
          if (!(e instanceof UnitError)) throw e;
          reject('unknown_unit', `package_coverage_unit "${pkgUnitText}" is not a unit`);
        }
        if (!sameDimension(coverageUnit, usableUnit)) {
          reject(
            'unit_dimension_mismatch',
            `package coverage unit ${coverageUnit} (${UNITS[coverageUnit].dimension}) does not match usable_unit ${usableUnit} (${UNITS[usableUnit].dimension})`,
          );
        }
        if (pkgLabel === '') rowWarn('package_label_defaulted', 'package_label is empty; labelled "package"');
        pkg = { label: pkgLabel === '' ? 'package' : pkgLabel, coverage_qty: coverageQty, coverage_unit: coverageUnit };
      }

      const trade = get('trade') || null;
      const manufacturer = get('manufacturer') || null;
      const model = get('model') || null;
      const notes = get('notes') || null;

      // Supplier item -------------------------------------------------------
      const supplierName = get('supplier');
      const sku = get('sku') || null;
      const urlText = get('url');
      let url = null;
      if (urlText !== '') {
        url = safeUrl(urlText);
        if (url === null) rowWarn('url_rejected', 'url is not an http(s) link and was not imported');
      }

      // Price observation ---------------------------------------------------
      const priceText = get('price');
      let priceCents;
      try {
        priceCents = parsePriceCents(priceText);
      } catch (e) {
        if (!(e instanceof CsvError)) throw e;
        reject(e.code === 'NEGATIVE_PRICE' ? 'negative_price' : 'invalid_price', e.message);
      }
      let obsDraft = null;
      if (priceCents === null) {
        rowWarn('no_price', 'price is empty: product imported without a price observation (it stays UNPRICED, not $0)');
      } else {
        if (priceCents === 0) reject('zero_price', 'price is 0; leave price empty if it is unknown');
        if (supplierName === '') reject('missing_supplier', 'a price needs a supplier');

        const perText = get('price_per');
        if (perText === '') reject('missing_price_per', 'price_per is empty (use "package" or "usable_unit")');
        const per = resolvePricePer(perText, usableUnit, pkg);
        if (per === null) {
          reject('invalid_price_per', `price_per "${perText}" must be "package", "usable_unit" or the usable unit ${usableUnit}`);
        }
        if (per === 'package' && pkg === null) reject('missing_package', 'price_per is "package" but no package coverage is given');

        const currencyText = get('currency').toUpperCase();
        if (currencyText === '') rowWarn('currency_assumed_usd', 'currency is empty; treated as USD');
        else if (currencyText !== 'USD') {
          reject('unsupported_currency', `currency "${currencyText}" is not supported (USD only; no conversion is done)`);
        }

        const observedAt = get('observed_at');
        if (observedAt === '') reject('missing_observed_at', 'a price needs an observed_at date (YYYY-MM-DD)');
        if (!isValidIsoDate(observedAt)) reject('invalid_observed_at', `observed_at "${observedAt}" is not a valid YYYY-MM-DD date`);

        const sourceTypeText = get('source_type');
        let sourceType = 'owner_import';
        if (sourceTypeText === '') rowWarn('source_type_defaulted', 'source_type is empty; recorded as owner_import');
        else {
          sourceType = normalizeEnum(sourceTypeText);
          if (!SOURCE_TYPES.includes(sourceType)) {
            reject('invalid_source_type', `source_type "${sourceTypeText}" must be one of ${SOURCE_TYPES.join(', ')}`);
          }
        }

        const availabilityText = get('availability');
        const availability = availabilityText === '' ? 'unknown' : normalizeEnum(availabilityText);
        if (!AVAILABILITY.includes(availability)) {
          reject('invalid_availability', `availability "${availabilityText}" must be one of ${AVAILABILITY.join(', ')}`);
        }

        const license = get('source_license') || (defaultLicense ? String(defaultLicense) : null);
        if (!license) rowWarn('missing_source_license', 'no source_license recorded for this price');

        const zip = get('zip') || null;
        if (zip !== null && !/^\d{5}(-\d{4})?$/.test(zip)) rowWarn('zip_format', `zip "${zip}" is not a 5-digit US ZIP code`);

        obsDraft = {
          price_cents: priceCents,
          currency: 'USD',
          per,
          store_id: get('store_id') || null,
          zip,
          availability,
          observed_at: observedAt,
          source_type: sourceType,
          source_ref: `${baseRef}#row=${record.line}`,
          source_license: license,
          reliability: reliabilityFor(sourceType),
          tax_included: false,
          shipping_included: false,
        };

        if (importedAt) {
          const detail = priceFreshnessDetail(obsDraft, importedAt);
          if (detail.reason === 'observed_after_as_of') {
            rowWarn('observed_after_import', `observed_at ${observedAt} is after the import date; it cannot be used until then`);
          } else if (detail.status !== 'fresh') {
            rowWarn(`price_${detail.status}`, `price is ${detail.status} at import: ${detail.age_days} days old (${sourceType} policy)`);
          }
        }
      }

      // Commit (only after every check passed) ------------------------------
      const productKey = JSON.stringify([
        materialKey,
        name.toLowerCase(),
        manufacturer?.toLowerCase() ?? '',
        model?.toLowerCase() ?? '',
        usableUnit,
        pkg ? [pkg.label.toLowerCase(), pkg.coverage_qty, pkg.coverage_unit] : null,
      ]);
      let product = productIndex.get(productKey);
      let item = null;
      let itemKey = null;
      if (supplierName !== '') {
        const productId = product?.id ?? allocate('prd', productKey);
        itemKey = JSON.stringify([productId, supplierName.toLowerCase(), (sku ?? url ?? '').toLowerCase()]);
        item = itemIndex.get(itemKey) ?? null;
      }
      if (obsDraft) {
        const itemId = item?.id ?? allocate('si', itemKey);
        const dupKey = JSON.stringify([itemId, obsDraft.store_id ?? '', obsDraft.observed_at]);
        const firstRow = observationIndex.get(dupKey);
        if (firstRow !== undefined) {
          reject('duplicate_observation', `same supplier item, store and observed_at as row ${firstRow}`);
        }
        observationIndex.set(dupKey, record.line);
      }

      if (!product) {
        product = {
          id: allocate('prd', productKey),
          material_key: materialKey,
          trade,
          name,
          spec: {},
          manufacturer,
          model,
          usable_unit: usableUnit,
          package: pkg,
          price_class: priceClass,
          alternates: [],
          notes,
          source_ref: `${baseRef}#row=${record.line}`,
        };
        productIndex.set(productKey, product);
        products.push(product);
      }
      if (supplierName !== '' && !item) {
        item = {
          id: allocate('si', itemKey),
          product_id: product.id,
          supplier_id: `sup_${slugify(supplierName) || fnv1a(supplierName.toLowerCase())}`,
          supplier_name: supplierName,
          sku,
          url,
          match_confidence: 1,
          match_method: 'manual',
        };
        itemIndex.set(itemKey, item);
        supplierItems.push(item);
      }
      if (obsDraft) {
        const obsKey = JSON.stringify([item.id, obsDraft.store_id ?? '', obsDraft.observed_at, obsDraft.per, obsDraft.price_cents, baseRef]);
        observations.push({
          id: allocate('obs', obsKey),
          supplier_item_id: item.id,
          ...obsDraft,
          imported_at: importedAt ?? null,
          notes,
        });
      } else {
        unpricedRows++;
      }
      warnings.push(...rowWarnings);
    } catch (e) {
      if (!(e instanceof RowRejection)) throw e;
      rejected.push({ row: record.line, code: e.code, reason: e.message });
    }
  }

  if (rows === 0) warn(null, 'no_data_rows', 'CSV has a header but no data rows');

  return {
    products,
    supplierItems,
    observations,
    report: {
      rows,
      imported: rows - rejected.length,
      rejected,
      warnings,
      products_created: products.length,
      supplier_items_created: supplierItems.length,
      observations_created: observations.length,
      unpriced_rows: unpricedRows,
      ignored_columns: ignored,
    },
  };
}

// ---------------------------------------------------------------------------
// Writing
// ---------------------------------------------------------------------------

// Leading characters that make spreadsheets evaluate a cell as a formula, plus their
// full-width forms, which some spreadsheet apps fold to ASCII.
const FORMULA_TRIGGERS = new Set(['=', '+', '-', '@', '\t', '\r', '＝', '＋', '－', '＠']);

/**
 * Neutralise spreadsheet formula injection: text starting with = + - @ tab or CR
 * (or a full-width = + - @) gets a leading apostrophe so it is shown as text.
 */
export function neutralizeFormula(text) {
  const s = String(text);
  return s.length > 0 && FORMULA_TRIGGERS.has(s[0]) ? `'${s}` : s;
}

function cellText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : ''; // numbers cannot carry a formula
  if (typeof value === 'bigint') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value.toISOString() : '';
  if (Array.isArray(value)) return neutralizeFormula(value.map((v) => (v === null || v === undefined ? '' : String(v))).join('; '));
  if (typeof value === 'object') return neutralizeFormula(JSON.stringify(value));
  return neutralizeFormula(String(value));
}

function quote(text) {
  return `"${text.replace(/"/g, '""')}"`;
}

/**
 * Serialise rows to CSV: every field quoted, quotes doubled, CRLF line endings
 * (including after the last row), formula-like text neutralised with a leading
 * apostrophe. `rows` is an array of arrays, or an array of objects — then a header
 * row is written from `options.columns` (default: keys in first-seen order).
 * null/undefined and non-finite numbers become empty cells (never 0); numbers are
 * written as-is; arrays are joined with '; '.
 */
export function toCsv(rows, { columns } = {}) {
  if (!Array.isArray(rows)) throw new CsvError('toCsv expects an array of rows', 'INVALID_INPUT');
  const allArrays = rows.every((r) => Array.isArray(r));
  const allObjects = rows.every((r) => r !== null && typeof r === 'object' && !Array.isArray(r));
  let lines;
  if (rows.length === 0) {
    lines = columns ? [columns.map(cellText)] : [];
  } else if (allArrays) {
    lines = (columns ? [columns, ...rows] : rows).map((row) => row.map(cellText));
  } else if (allObjects) {
    const keys = columns ?? [...new Set(rows.flatMap((r) => Object.keys(r)))];
    lines = [keys.map(cellText), ...rows.map((r) => keys.map((k) => cellText(r[k])))];
  } else {
    throw new CsvError('toCsv rows must all be arrays or all be objects', 'INVALID_INPUT');
  }
  return lines.map((cells) => `${cells.map(quote).join(',')}\r\n`).join('');
}

/** Integer cents as a plain decimal dollar string for spreadsheets: 123456 -> '1234.56'; null -> ''. */
export function centsToDecimal(cents) {
  if (cents === null || cents === undefined) return '';
  if (!Number.isSafeInteger(cents)) throw new CsvError(`Expected integer cents, got ${String(cents)}`, 'INVALID_AMOUNT');
  const sign = cents < 0 ? '-' : '';
  const abs = Math.abs(cents);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`;
}

/** Column order of exportBomCsv (an allowlist: no other line fields are ever exported). */
export const BOM_EXPORT_COLUMNS = Object.freeze([
  'line_id',
  'assembly_id',
  'layer',
  'material_key',
  'description',
  'qty',
  'unit',
  'waste_pct',
  'qty_with_waste',
  'packages',
  'package_label',
  'priced_qty',
  'priced_unit',
  'price_status',
  'unit_cost_cents',
  'unit_cost_basis',
  'extended_cents',
  'extended_usd',
  'display_amount',
  'freshness',
  'source_type',
  'supplier',
  'observed_at',
  'store_id',
  'source_ref',
  'quote_id',
  'price_class',
  'needs',
  'assumptions',
  'formula',
  'measurement_ids',
]);

const QTY_FIELDS = new Set(['qty', 'qty_with_waste', 'priced_qty']);

function bomRow(line) {
  const provenance = line.provenance ?? {};
  return BOM_EXPORT_COLUMNS.map((column) => {
    switch (column) {
      case 'extended_usd':
        return Number.isSafeInteger(line.extended_cents) ? centsToDecimal(line.extended_cents) : '';
      case 'source_type':
      case 'supplier':
      case 'observed_at':
      case 'store_id':
      case 'source_ref':
        return provenance[column] ?? null;
      case 'measurement_ids':
        return Array.isArray(line.measurement_ids) ? line.measurement_ids.join('+') : line.measurement_ids ?? null;
      default: {
        const value = line[column];
        if (QTY_FIELDS.has(column) && typeof value === 'number' && Number.isFinite(value)) return roundQty(value);
        return value ?? null;
      }
    }
  });
}

function summaryRows(estimate) {
  const t = estimate.totals;
  const usd = (cents) => (Number.isSafeInteger(cents) ? centsToDecimal(cents) : 'NOT SET');
  const rows = [
    ['ESTIMATE SUMMARY', ''],
    ['as_of', estimate.as_of ?? ''],
    ['status', t.is_complete ? 'COMPLETE' : 'INCOMPLETE — totals cover known amounts only'],
    ['is_complete', t.is_complete === true],
    ['confirmed_usd', usd(t.confirmed_cents)],
    ['provisional_usd', usd(t.provisional_cents)],
    ['allowance_usd', usd(t.allowance_cents)],
  ];
  if (t.labor_cents !== undefined) rows.push(['labor_usd', usd(t.labor_cents)]);
  if (t.shipping_cents !== undefined) rows.push(['shipping_usd', usd(t.shipping_cents)]);
  rows.push(
    ['tax_usd', usd(t.tax_cents)],
    ['markup_usd', usd(t.markup_cents)],
    ['contingency_usd', usd(t.contingency_cents)],
    ['grand_total_known_usd', usd(t.grand_total_known_cents)],
    ['missing_lines', t.missing_lines ?? ''],
    ['provisional_lines', t.provisional_lines ?? ''],
    ['by_owner_lines', t.by_owner_lines ?? ''],
  );
  if (t.display_total !== undefined) rows.push(['display_total', t.display_total]);
  if (t.display_note !== undefined) rows.push(['display_note', t.display_note]);
  return rows;
}

/**
 * Export an estimate (from estimate.buildEstimate) — or a plain array of BOM lines — as CSV.
 * One row per line using BOM_EXPORT_COLUMNS only (private fields such as quote attachments
 * are never written). Missing amounts are empty cells with price_status/display_amount
 * saying why, never 0. Quantities are rounded to 4 dp. With `{summary:true}` (default) and
 * totals present, a blank row and a two-column summary block follow (padded to the same width),
 * with unset tax/shipping written as 'NOT SET'.
 */
export function exportBomCsv(estimate, { summary = true } = {}) {
  const lines = Array.isArray(estimate) ? estimate : estimate?.lines;
  if (!Array.isArray(lines)) throw new CsvError('exportBomCsv expects an estimate with a lines array', 'INVALID_INPUT');
  const rows = lines.map((line, index) => {
    if (!line || typeof line !== 'object') throw new CsvError(`Line ${index} is not an object`, 'INVALID_INPUT');
    return bomRow(line);
  });
  if (summary && !Array.isArray(estimate) && estimate.totals && typeof estimate.totals === 'object') {
    const width = BOM_EXPORT_COLUMNS.length;
    const pad = (row) => [...row, ...Array(width - row.length).fill('')];
    rows.push(pad([]), ...summaryRows(estimate).map(pad));
  }
  return toCsv(rows, { columns: [...BOM_EXPORT_COLUMNS] });
}
