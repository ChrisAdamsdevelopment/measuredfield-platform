// MeasuredField Remodel Takeoff | project document (schema mf.takeoff.project/2).
//
// A project document is the single JSON file that holds everything a takeoff
// needs: project facts and code-basis inputs, sheets, calibrations, measurements,
// overrides, pricing selections, catalog, quotes and estimate settings.
//
// Rules enforced here:
//   - Code basis inputs (ahj_id, application_date, approved_plan_basis) are
//     project facts. They are NEVER defaulted from today's date; unknown is null.
//   - Money is integer cents; a non-integer price/amount is a validation error.
//   - Legacy v1 quick-calculator results are migrated without inventing data:
//     typed dimensions are preserved as a legacy record, never turned into
//     plan measurements, and no code basis is inferred.
//   - snapshotHash() is a stable SHA-256 over canonical JSON (sorted keys), so
//     the same content always hashes the same regardless of key order.
//
// Pure logic, no DOM. Runs in Node 22 and modern browsers.

/** Version of this module. */
export const MODULE_VERSION = '0.2.0';

/** Application version written into project documents. */
export const APP_VERSION = '0.2.0';

/** Schema identifier of the current project document. */
export const PROJECT_SCHEMA = 'mf.takeoff.project/2';

/** Largest project JSON text parseProject accepts (characters). */
export const MAX_PROJECT_CHARS = 25 * 1024 * 1024;

/** Collections that are always arrays in a v2 project. */
export const PROJECT_COLLECTIONS = Object.freeze([
  'sheets', 'calibrations', 'calibration_checks', 'measurements', 'overrides', 'selections', 'quotes',
]);

const KNOWN_SCOPES = Object.freeze(['bath', 'kitchen', 'interior', 'garage', 'deck']);
const MEASUREMENT_KINDS = Object.freeze(['line', 'polyline', 'polygon', 'count']);
const MEASUREMENT_LAYERS = Object.freeze(['existing', 'demolish', 'new', 'field_verify']);
const SELECTION_MODES = Object.freeze(['retail', 'contractor_rate', 'supplier_quote', 'allowance', 'by_owner', 'unpriced']);
const CATALOG_KEYS = Object.freeze(['products', 'supplierItems', 'observations']);

/** Default estimate settings: tax is "not set" (null), never silently 0. */
export const DEFAULT_SETTINGS = Object.freeze({
  tax_rate_pct: null,
  markup_pct: 0,
  contingency_pct: 0,
  shipping_cents: null,
  labor: Object.freeze([]),
});

/** Error thrown for invalid or unsupported project documents; `errors` lists every problem found. */
export class ProjectError extends Error {
  constructor(message, errors = []) {
    super(message);
    this.name = 'ProjectError';
    this.errors = errors;
  }
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isNonEmptyString = (v) => typeof v === 'string' && v.trim() !== '';
const clone = (v) => (v === undefined ? undefined : structuredClone(v));

/** True for a real calendar date written as YYYY-MM-DD. */
export function isIsoDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

/** True for an ISO-8601 timestamp string (YYYY-MM-DDTHH:MM...). */
function isIsoTimestamp(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) && Number.isFinite(Date.parse(value));
}

/** Resolve an explicit timestamp or fall back to the current instant (timestamps only, never code basis). */
function timestamp(at) {
  if (at === undefined || at === null) return new Date().toISOString();
  if (at instanceof Date) {
    if (!Number.isFinite(at.getTime())) throw new ProjectError('Invalid timestamp');
    return at.toISOString();
  }
  if (!isIsoTimestamp(at)) throw new ProjectError('Timestamp must be an ISO-8601 string: ' + String(at));
  return at;
}

/** Generate a random id with a readable prefix (crypto.randomUUID when available). */
export function newId(prefix = 'id') {
  const c = globalThis.crypto;
  let body;
  if (c && typeof c.randomUUID === 'function') body = c.randomUUID();
  else if (c && typeof c.getRandomValues === 'function') {
    body = Array.from(c.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
  } else {
    body = Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }
  return prefix + '_' + body;
}

/** JSON.parse reviver that drops `__proto__` keys so parsed data can never alter prototypes. */
function safeReviver(key, value) {
  return key === '__proto__' ? undefined : value;
}

// ---------------------------------------------------------------------------
// Creation
// ---------------------------------------------------------------------------

/**
 * Create a new, empty v2 project document.
 * `application_date` stays null unless given (it is never taken from today's date);
 * `at` (ISO string or Date) sets created_at/updated_at, defaulting to now.
 */
export function createProject({
  id,
  name,
  scope = null,
  ahj_id = null,
  application_date = null,
  approved_plan_basis = null,
  settings,
  at,
} = {}) {
  const when = timestamp(at);
  const project = {
    schema: PROJECT_SCHEMA,
    app_version: APP_VERSION,
    id: isNonEmptyString(id) ? id : newId('prj'),
    name: isNonEmptyString(name) ? name.trim() : 'Untitled project',
    scope: scope ?? null,
    ahj_id: isNonEmptyString(ahj_id) ? ahj_id : null,
    application_date: application_date ?? null,
    approved_plan_basis: clone(approved_plan_basis) ?? null,
    sheets: [],
    calibrations: [],
    calibration_checks: [],
    measurements: [],
    overrides: [],
    selections: [],
    catalog: { products: [], supplierItems: [], observations: [] },
    quotes: [],
    settings: { ...DEFAULT_SETTINGS, labor: [], ...clone(settings ?? {}) },
    created_at: when,
    updated_at: when,
  };
  const { errors } = validateProject(project);
  if (errors.length) throw new ProjectError('Invalid project: ' + errors[0], errors);
  return project;
}

/** Return a copy of the project with updated_at set to `at` (default now). */
export function touchProject(project, at) {
  return { ...project, updated_at: timestamp(at) };
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/**
 * Validate a v2 project document.
 * Returns {errors, warnings}; errors make the document unusable, warnings need review.
 */
export function validateProject(project) {
  const errors = [];
  const warnings = [];
  if (!isPlainObject(project)) return { errors: ['project must be a JSON object'], warnings };

  if (project.schema !== PROJECT_SCHEMA) errors.push(`schema must be '${PROJECT_SCHEMA}'`);
  if (!isNonEmptyString(project.id)) errors.push('id must be a non-empty string');
  else if (project.id.length > 200) errors.push('id is longer than 200 characters');
  if (typeof project.name !== 'string') errors.push('name must be a string');
  else if (project.name.length > 200) errors.push('name is longer than 200 characters');
  if (typeof project.app_version !== 'string') errors.push('app_version must be a string');

  if (project.scope !== null && typeof project.scope !== 'string') errors.push('scope must be a string or null');
  else if (project.scope !== null && !KNOWN_SCOPES.includes(project.scope)) warnings.push(`scope '${project.scope}' is not a known pilot scope`);

  // Code basis inputs: project facts only.
  if (project.ahj_id !== null && !isNonEmptyString(project.ahj_id)) errors.push('ahj_id must be a non-empty string or null');
  if (project.application_date !== null && !isIsoDate(project.application_date)) {
    errors.push('application_date must be a YYYY-MM-DD calendar date or null');
  }
  if (project.ahj_id === null) warnings.push('ahj_id not set: code basis will be UNKNOWN');
  if (project.application_date === null) warnings.push('application_date not set: code basis will be UNKNOWN');
  validateApprovedPlanBasis(project.approved_plan_basis, errors);

  for (const key of ['created_at', 'updated_at']) {
    if (!isIsoTimestamp(project[key])) errors.push(`${key} must be an ISO-8601 timestamp`);
  }
  if (isIsoTimestamp(project.created_at) && isIsoTimestamp(project.updated_at)
    && Date.parse(project.updated_at) < Date.parse(project.created_at)) {
    warnings.push('updated_at is earlier than created_at');
  }

  let collectionsOk = true;
  for (const key of PROJECT_COLLECTIONS) {
    if (!Array.isArray(project[key])) {
      errors.push(`${key} must be an array`);
      collectionsOk = false;
      continue;
    }
    project[key].forEach((item, i) => {
      if (!isPlainObject(item)) errors.push(`${key}[${i}] must be an object`);
    });
  }
  if (collectionsOk) validateCollections(project, errors, warnings);
  validateCatalogShape(project.catalog, errors);
  validateSettings(project.settings, errors, warnings);

  return { errors, warnings };
}

function validateApprovedPlanBasis(basis, errors) {
  if (basis === null) return;
  if (!isPlainObject(basis)) {
    errors.push('approved_plan_basis must be an object or null');
    return;
  }
  if (basis.accepted_on !== undefined && basis.accepted_on !== null && !isIsoDate(basis.accepted_on)) {
    errors.push('approved_plan_basis.accepted_on must be a YYYY-MM-DD calendar date or null');
  }
  if (basis.editions !== undefined && basis.editions !== null && typeof basis.editions !== 'object') {
    errors.push('approved_plan_basis.editions must be an object or array');
  }
}

/** Check that every object in `list` has a unique non-empty id. Returns the id set. */
function checkIds(list, key, errors, { required = true } = {}) {
  const ids = new Set();
  list.forEach((item, i) => {
    if (!isPlainObject(item)) return;
    if (item.id === undefined || item.id === null) {
      if (required) errors.push(`${key}[${i}].id is required`);
      return;
    }
    if (!isNonEmptyString(item.id)) {
      errors.push(`${key}[${i}].id must be a non-empty string`);
      return;
    }
    if (ids.has(item.id)) errors.push(`${key}[${i}].id duplicates '${item.id}'`);
    ids.add(item.id);
  });
  return ids;
}

function validateCollections(project, errors, warnings) {
  const sheetIds = checkIds(project.sheets, 'sheets', errors);
  checkIds(project.calibrations, 'calibrations', errors, { required: false });
  checkIds(project.measurements, 'measurements', errors);
  checkIds(project.quotes, 'quotes', errors);

  project.calibrations.forEach((c, i) => {
    if (!isPlainObject(c)) return;
    if (!isNonEmptyString(c.sheet_id)) errors.push(`calibrations[${i}].sheet_id is required`);
    else if (!sheetIds.has(c.sheet_id)) warnings.push(`calibrations[${i}] refers to unknown sheet '${c.sheet_id}'`);
    if (c.ft_per_pt !== undefined && !(Number.isFinite(c.ft_per_pt) && c.ft_per_pt > 0)) {
      errors.push(`calibrations[${i}].ft_per_pt must be a positive number`);
    }
  });

  project.measurements.forEach((m, i) => {
    if (!isPlainObject(m)) return;
    const at = `measurements[${i}]`;
    if (!isNonEmptyString(m.sheet_id)) errors.push(`${at}.sheet_id is required`);
    else if (!sheetIds.has(m.sheet_id)) warnings.push(`${at} refers to unknown sheet '${m.sheet_id}'`);
    if (!MEASUREMENT_KINDS.includes(m.kind)) errors.push(`${at}.kind must be one of ${MEASUREMENT_KINDS.join('|')}`);
    if (m.layer !== undefined && !MEASUREMENT_LAYERS.includes(m.layer)) errors.push(`${at}.layer must be one of ${MEASUREMENT_LAYERS.join('|')}`);
    if (!Array.isArray(m.points)) errors.push(`${at}.points must be an array`);
    else if (!m.points.every((p) => isPlainObject(p) && Number.isFinite(p.x) && Number.isFinite(p.y))) {
      errors.push(`${at}.points must contain only finite {x, y} points`);
    }
  });

  project.overrides.forEach((o, i) => {
    if (!isPlainObject(o)) return;
    if (!isNonEmptyString(o.line_id)) errors.push(`overrides[${i}].line_id is required`);
    if (!isNonEmptyString(o.reason)) errors.push(`overrides[${i}].reason must be a non-empty explanation`);
    if (!(Number.isFinite(o.qty) && o.qty >= 0)) errors.push(`overrides[${i}].qty must be a finite number >= 0`);
  });

  const selectedLines = new Set();
  project.selections.forEach((s, i) => {
    if (!isPlainObject(s)) return;
    if (!isNonEmptyString(s.line_id)) errors.push(`selections[${i}].line_id is required`);
    else if (selectedLines.has(s.line_id)) errors.push(`selections[${i}] duplicates the selection for line '${s.line_id}'`);
    else selectedLines.add(s.line_id);
    if (!SELECTION_MODES.includes(s.mode)) errors.push(`selections[${i}].mode must be one of ${SELECTION_MODES.join('|')}`);
    if (s.allowance_cents !== undefined && s.allowance_cents !== null && !Number.isInteger(s.allowance_cents)) {
      errors.push(`selections[${i}].allowance_cents must be integer cents`);
    }
    if (s.rate_cents_per_unit !== undefined && s.rate_cents_per_unit !== null && !Number.isInteger(s.rate_cents_per_unit)) {
      errors.push(`selections[${i}].rate_cents_per_unit must be integer cents`);
    }
  });

  project.quotes.forEach((q, i) => {
    if (!isPlainObject(q)) return;
    if (!Array.isArray(q.line_ids)) errors.push(`quotes[${i}].line_ids must be an array`);
    if (q.amount_cents !== undefined && q.amount_cents !== null && !(Number.isInteger(q.amount_cents) && q.amount_cents >= 0)) {
      errors.push(`quotes[${i}].amount_cents must be integer cents >= 0 or null`);
    }
    if (q.valid_until !== undefined && q.valid_until !== null && !isIsoDate(q.valid_until) && !isIsoTimestamp(q.valid_until)) {
      errors.push(`quotes[${i}].valid_until must be a date or null`);
    }
  });
}

function validateCatalogShape(catalog, errors) {
  if (!isPlainObject(catalog)) {
    errors.push('catalog must be an object {products, supplierItems, observations}');
    return;
  }
  for (const key of CATALOG_KEYS) {
    if (!Array.isArray(catalog[key])) errors.push(`catalog.${key} must be an array`);
  }
  if (Array.isArray(catalog.observations)) {
    catalog.observations.forEach((o, i) => {
      if (!isPlainObject(o)) errors.push(`catalog.observations[${i}] must be an object`);
      else if (o.price_cents !== undefined && o.price_cents !== null && !Number.isInteger(o.price_cents)) {
        errors.push(`catalog.observations[${i}].price_cents must be integer cents`);
      }
    });
  }
}

function validateSettings(settings, errors, warnings) {
  if (!isPlainObject(settings)) {
    errors.push('settings must be an object');
    return;
  }
  if (settings.tax_rate_pct === null || settings.tax_rate_pct === undefined) warnings.push('tax rate not set');
  else if (!(Number.isFinite(settings.tax_rate_pct) && settings.tax_rate_pct >= 0)) errors.push('settings.tax_rate_pct must be a number >= 0 or null');
  for (const key of ['markup_pct', 'contingency_pct']) {
    if (settings[key] !== undefined && !(Number.isFinite(settings[key]) && settings[key] >= 0)) {
      errors.push(`settings.${key} must be a number >= 0`);
    }
  }
  if (settings.shipping_cents !== undefined && settings.shipping_cents !== null
    && !(Number.isInteger(settings.shipping_cents) && settings.shipping_cents >= 0)) {
    errors.push('settings.shipping_cents must be integer cents >= 0 or null');
  }
  if (settings.labor !== undefined && !Array.isArray(settings.labor)) errors.push('settings.labor must be an array');
}

// ---------------------------------------------------------------------------
// Serialization, parsing and migration
// ---------------------------------------------------------------------------

/**
 * Serialize a valid project to pretty JSON text (stamped with this APP_VERSION).
 * Throws ProjectError if the project does not validate.
 */
export function serializeProject(project, { space = 2 } = {}) {
  const { errors } = validateProject(project);
  if (errors.length) throw new ProjectError('Cannot save invalid project: ' + errors[0], errors);
  return JSON.stringify({ ...project, app_version: APP_VERSION }, null, space) + '\n';
}

/** True if `doc` looks like a legacy quick-calculator result (quantities.mjs calculateTakeoff output). */
export function isQuickResult(doc) {
  return isPlainObject(doc)
    && doc.schema === undefined
    && typeof doc.version === 'string'
    && /^0\.[12]\./.test(doc.version)
    && typeof doc.scope === 'string'
    && Array.isArray(doc.items);
}

/**
 * Migrate a legacy v1 quick-calculator result into a v2 project.
 * Typed dimensions are kept verbatim in `legacy_quick_result`; no measurements,
 * sheets, prices or code basis are invented. Returns the new project.
 */
export function migrateQuickResult(result, { at, id } = {}) {
  if (!isQuickResult(result)) throw new ProjectError('Not a quick-calculator result');
  const problems = [];
  result.items.forEach((item, i) => {
    if (!isPlainObject(item) || !isNonEmptyString(item.id) || !Number.isFinite(item.qty) || typeof item.unit !== 'string') {
      problems.push(`items[${i}] must have id, finite qty and unit`);
    }
  });
  if (problems.length) throw new ProjectError('Malformed quick-calculator result: ' + problems[0], problems);

  const when = timestamp(at);
  const ctx = isPlainObject(result.context) ? result.context : {};
  const notes = [
    'Quick-calculator dimensions were typed, not measured on a calibrated sheet; they were not converted into plan measurements.',
  ];

  let ahj = null;
  if (isNonEmptyString(ctx.ahj) && !['unknown', 'other'].includes(ctx.ahj)) ahj = ctx.ahj;
  else if (ctx.ahj === 'other') notes.push("AHJ was recorded as 'other': select the actual authority having jurisdiction.");

  let applicationDate = null;
  if (ctx.applicationDate !== undefined && ctx.applicationDate !== null && ctx.applicationDate !== '') {
    if (isIsoDate(ctx.applicationDate)) applicationDate = ctx.applicationDate;
    else notes.push('Recorded application date was not a valid YYYY-MM-DD date and was dropped.');
  }
  if (applicationDate === null) notes.push('No application date recorded: code basis stays UNKNOWN until one is entered.');

  const scope = KNOWN_SCOPES.includes(result.scope) ? result.scope : null;
  if (scope === null) notes.push(`Legacy scope '${result.scope}' is not recognized and was not carried over.`);

  const project = createProject({
    id,
    name: isNonEmptyString(ctx.projectLabel) ? ctx.projectLabel.slice(0, 200) : 'Migrated quick takeoff',
    scope,
    ahj_id: ahj,
    application_date: applicationDate,
    at: when,
  });
  project.legacy_quick_result = clone(result);
  project.migration = { from: 'quick-result/' + result.version, to: PROJECT_SCHEMA, at: when, notes };
  return project;
}

/**
 * Parse project JSON text (or an already-parsed object), migrate legacy quick
 * results, fill missing collections with empty defaults and validate.
 * Returns a fresh v2 project object; throws ProjectError when unusable.
 */
export function parseProject(input, { at } = {}) {
  let doc;
  if (typeof input === 'string') {
    if (input.length > MAX_PROJECT_CHARS) throw new ProjectError('Project file is too large');
    try {
      doc = JSON.parse(input.replace(/^﻿/, ''), safeReviver);
    } catch (err) {
      throw new ProjectError('Project file is not valid JSON: ' + err.message);
    }
  } else if (isPlainObject(input)) {
    try {
      doc = JSON.parse(JSON.stringify(input), safeReviver);
    } catch (err) {
      throw new ProjectError('Project object cannot be represented as JSON: ' + err.message);
    }
  } else {
    throw new ProjectError('Project must be JSON text or an object');
  }
  if (!isPlainObject(doc)) throw new ProjectError('Project file must contain a JSON object');

  if (isQuickResult(doc)) return migrateQuickResult(doc, { at });

  if (doc.schema === undefined) throw new ProjectError('Unrecognized file: no project schema and not a quick-calculator result');
  const match = typeof doc.schema === 'string' ? /^mf\.takeoff\.project\/(\d+)$/.exec(doc.schema) : null;
  if (match && Number(match[1]) > 2) {
    throw new ProjectError(`Project schema version ${match[1]} is newer than this app (${APP_VERSION}) supports`);
  }
  if (doc.schema !== PROJECT_SCHEMA) throw new ProjectError('Unsupported project schema: ' + String(doc.schema));

  // Fill defaults for optional parts so older v2 drafts stay loadable.
  for (const key of PROJECT_COLLECTIONS) if (doc[key] === undefined) doc[key] = [];
  if (doc.catalog === undefined) doc.catalog = {};
  if (isPlainObject(doc.catalog)) for (const key of CATALOG_KEYS) if (doc.catalog[key] === undefined) doc.catalog[key] = [];
  if (doc.settings === undefined) doc.settings = { ...DEFAULT_SETTINGS, labor: [] };
  for (const key of ['scope', 'ahj_id', 'application_date', 'approved_plan_basis']) if (doc[key] === undefined) doc[key] = null;
  if (doc.app_version === undefined) doc.app_version = 'unknown';

  const { errors } = validateProject(doc);
  if (errors.length) throw new ProjectError(`Invalid project (${errors.length} problem${errors.length === 1 ? '' : 's'}): ${errors[0]}`, errors);
  return doc;
}

// ---------------------------------------------------------------------------
// Stable hashing
// ---------------------------------------------------------------------------

/**
 * Canonical JSON: object keys sorted (by UTF-16 code units), no whitespace.
 * Follows JSON.stringify for toJSON/undefined/functions, but throws on values that
 * JSON would silently corrupt (NaN/Infinity, BigInt, Map/Set, circular references).
 */
export function stableStringify(value) {
  const stack = new Set();
  const encode = (v, inArray) => {
    if (v !== null && typeof v === 'object' && typeof v.toJSON === 'function') v = v.toJSON();
    if (v === null) return 'null';
    switch (typeof v) {
      case 'string':
        return JSON.stringify(v);
      case 'boolean':
        return v ? 'true' : 'false';
      case 'number':
        if (!Number.isFinite(v)) throw new TypeError('stableStringify: non-finite number cannot be hashed');
        return JSON.stringify(v);
      case 'bigint':
        throw new TypeError('stableStringify: BigInt cannot be hashed');
      case 'undefined':
      case 'function':
      case 'symbol':
        return inArray ? 'null' : undefined;
      default:
        break;
    }
    if (v instanceof Map || v instanceof Set) throw new TypeError('stableStringify: Map/Set cannot be hashed; convert to arrays');
    if (stack.has(v)) throw new TypeError('stableStringify: circular reference');
    stack.add(v);
    let out;
    if (Array.isArray(v)) {
      // Array.from visits holes in sparse arrays (as undefined → null), like JSON.stringify.
      out = '[' + Array.from(v, (x) => encode(x, true)).join(',') + ']';
    } else {
      const parts = [];
      for (const key of Object.keys(v).sort()) {
        const encoded = encode(v[key], false);
        if (encoded !== undefined) parts.push(JSON.stringify(key) + ':' + encoded);
      }
      out = '{' + parts.join(',') + '}';
    }
    stack.delete(v);
    return out;
  };
  const text = encode(value, false);
  if (text === undefined) throw new TypeError('stableStringify: value is not serializable');
  return text;
}

/**
 * Async SHA-256 of the canonical JSON of `value` via globalThis.crypto.subtle.
 * Resolves to a 64-character lowercase hex string; identical content always gives
 * the identical hash regardless of object key order.
 */
export async function snapshotHash(value) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle || typeof subtle.digest !== 'function') {
    throw new Error('snapshotHash requires globalThis.crypto.subtle (Web Crypto)');
  }
  const bytes = new TextEncoder().encode(stableStringify(value));
  const digest = await subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}
