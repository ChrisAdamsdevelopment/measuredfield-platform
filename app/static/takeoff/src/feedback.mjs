// MeasuredField Remodel Takeoff | review feedback and estimate comparison (spec 3.10).
//
// A reviewer (contractor, estimator, owner's rep) leaves structured feedback on a
// specific estimate snapshot: a BOM line, a measurement, a sheet or the project in
// general. Feedback is exported as a self-verifying package (SHA-256 over canonical
// JSON) and two estimates can be compared line by line.
//
// Rules enforced here:
//   - Feedback on a line must name the estimate snapshot (estimate_hash) it was made
//     against, so a correction is never applied to a different set of numbers.
//   - Suggested units are typed (units.mjs). A suggestion in a different dimension
//     than the target line (e.g. SF for an LF line) is rejected, never converted.
//   - Unpriced never becomes $0: compareEstimates reports an unknown cost effect as
//     null and counts it, instead of treating a missing price as zero.
//   - Packages carry no plan files and no private quote attachments.
//
// Pure logic, no DOM. Runs in Node 22 and modern browsers.

import { APP_VERSION, newId, snapshotHash, validateProject } from './project.mjs';
import { convert, dimensionOf, isKnownUnit, normalizeUnit } from './units.mjs';

/** Version of this module. */
export const MODULE_VERSION = '0.2.0';

/** Schema identifier of an exported feedback package. */
export const FEEDBACK_PACKAGE_SCHEMA = 'mf.takeoff.feedback-package/1';

/** What a piece of feedback can point at. */
export const TARGET_TYPES = Object.freeze(['line', 'measurement', 'sheet', 'general']);

/** Kinds of feedback. */
export const FEEDBACK_KINDS = Object.freeze([
  'quantity_correction', 'missing_item', 'wrong_assumption', 'pricing', 'usability', 'other',
]);

/** Feedback review states. */
export const FEEDBACK_STATUSES = Object.freeze(['open', 'accepted', 'declined', 'done']);

/** Allowed status changes: from → [to]. Declined and done items can be reopened. */
export const STATUS_TRANSITIONS = Object.freeze({
  open: Object.freeze(['accepted', 'declined', 'done']),
  accepted: Object.freeze(['done', 'declined', 'open']),
  declined: Object.freeze(['open']),
  done: Object.freeze(['open']),
});

/** Longest comment accepted (characters). */
export const MAX_COMMENT_CHARS = 4000;

/** Longest reviewer label accepted (characters). */
export const MAX_REVIEWER_LABEL_CHARS = 80;

/** Most feedback items one package may carry. */
export const MAX_FEEDBACK_ITEMS = 5000;

/** Largest feedback package text parseFeedbackPackage accepts (characters). */
export const MAX_PACKAGE_CHARS = 10 * 1024 * 1024;

/** Error thrown for invalid feedback, packages or estimate comparisons; `errors` lists every problem found. */
export class FeedbackError extends Error {
  constructor(message, errors = []) {
    super(message);
    this.name = 'FeedbackError';
    this.errors = errors;
  }
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isNonEmptyString = (v) => typeof v === 'string' && v.trim() !== '';
const HASH_RE = /^[0-9a-f]{64}$/;
const QTY_EPSILON = 1e-9;

/** Round to 4 decimal places (stored-output precision) without producing -0. */
function round4(x) {
  const r = Math.round(x * 1e4) / 1e4;
  return r === 0 ? 0 : r;
}

function isIsoTimestamp(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) && Number.isFinite(Date.parse(value));
}

/** Resolve an explicit timestamp or fall back to the current instant (record timestamps only). */
function timestamp(at) {
  if (at === undefined || at === null) return new Date().toISOString();
  if (at instanceof Date) {
    if (!Number.isFinite(at.getTime())) throw new FeedbackError('Invalid timestamp');
    return at.toISOString();
  }
  if (!isIsoTimestamp(at)) throw new FeedbackError('Timestamp must be an ISO-8601 string: ' + String(at));
  return at;
}

/** Normalize free text: CRLF → LF, drop control characters other than tab/newline, trim. */
function cleanText(text) {
  return String(text)
    .replace(/\r\n?/g, '\n')
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .trim();
}

/** JSON.parse reviver that drops `__proto__` keys so parsed data can never alter prototypes. */
function safeReviver(key, value) {
  return key === '__proto__' ? undefined : value;
}

/** Compare two quantities with a relative tolerance for floating-point noise. */
function sameQty(a, b) {
  return Math.abs(a - b) <= QTY_EPSILON * Math.max(1, Math.abs(a), Math.abs(b));
}

/** Find a line by id in an estimate ({lines}) or a plain array of lines. */
function findLine(estimate, lineId) {
  const lines = Array.isArray(estimate) ? estimate : estimate?.lines;
  return Array.isArray(lines) ? lines.find((l) => isPlainObject(l) && l.line_id === lineId) ?? null : null;
}

/** Dimension of a unit, or null when the text is not a known unit (e.g. a package word). */
function dimensionOrNull(unit) {
  return isKnownUnit(unit) ? dimensionOf(unit) : null;
}

// ---------------------------------------------------------------------------
// Feedback records
// ---------------------------------------------------------------------------

/**
 * Validate one feedback record. Returns {errors, warnings}.
 * Options: `project` checks project_id and that measurement/sheet targets exist;
 * `estimate` checks line targets and unit dimensions; `strict` turns a missing
 * target into an error instead of a warning.
 */
export function validateFeedback(fb, { project = null, estimate = null, strict = false } = {}) {
  const errors = [];
  const warnings = [];
  if (!isPlainObject(fb)) return { errors: ['feedback must be an object'], warnings };

  if (!isNonEmptyString(fb.id)) errors.push('id must be a non-empty string');
  else if (fb.id.length > 200) errors.push('id is longer than 200 characters');
  if (typeof fb.app_version !== 'string') errors.push('app_version must be a string');
  if (!isNonEmptyString(fb.project_id)) errors.push('project_id must be a non-empty string');
  if (fb.estimate_hash !== null && !(typeof fb.estimate_hash === 'string' && HASH_RE.test(fb.estimate_hash))) {
    errors.push('estimate_hash must be a 64-character lowercase SHA-256 hex string or null');
  }

  const target = fb.target;
  if (!isPlainObject(target)) {
    errors.push('target must be an object {type, id}');
  } else if (!TARGET_TYPES.includes(target.type)) {
    errors.push(`target.type must be one of ${TARGET_TYPES.join('|')}`);
  } else if (target.type === 'general') {
    if (target.id !== null && target.id !== undefined) errors.push('target.id must be null for general feedback');
  } else if (!isNonEmptyString(target.id)) {
    errors.push(`target.id is required for ${target.type} feedback`);
  }
  const targetType = isPlainObject(target) ? target.type : undefined;
  if (targetType === 'line' && fb.estimate_hash === null) {
    errors.push('line feedback must reference the estimate_hash it was made against');
  }

  if (!FEEDBACK_KINDS.includes(fb.kind)) errors.push(`kind must be one of ${FEEDBACK_KINDS.join('|')}`);

  const suggested = fb.suggested;
  let hasQty = false;
  if (!isPlainObject(suggested)) {
    errors.push('suggested must be an object {qty?, unit?}');
  } else {
    for (const key of Object.keys(suggested)) {
      if (key !== 'qty' && key !== 'unit') errors.push(`suggested.${key} is not supported (only qty and unit)`);
    }
    if (suggested.qty !== undefined) {
      if (typeof suggested.qty === 'number' && Number.isFinite(suggested.qty) && suggested.qty >= 0) hasQty = true;
      else errors.push('suggested.qty must be a finite number >= 0');
    }
    if (suggested.unit !== undefined) {
      if (!isKnownUnit(suggested.unit)) errors.push(`suggested.unit '${String(suggested.unit)}' is not a known unit`);
      else if (suggested.qty === undefined) errors.push('suggested.unit given without suggested.qty');
    }
  }

  if (typeof fb.comment !== 'string') errors.push('comment must be a string');
  else if (fb.comment.length > MAX_COMMENT_CHARS) errors.push(`comment is longer than ${MAX_COMMENT_CHARS} characters`);

  if (fb.kind === 'quantity_correction') {
    if (targetType !== undefined && targetType !== 'line' && targetType !== 'measurement') {
      errors.push('quantity_correction must target a line or a measurement');
    }
    if (!hasQty) errors.push('quantity_correction requires suggested.qty');
  } else if (FEEDBACK_KINDS.includes(fb.kind) && !isNonEmptyString(fb.comment)) {
    errors.push(`comment is required for ${fb.kind} feedback`);
  }

  if (fb.reviewer_label === null) warnings.push('reviewer_label not set');
  else if (typeof fb.reviewer_label !== 'string') errors.push('reviewer_label must be a string or null');
  else if (fb.reviewer_label.length > MAX_REVIEWER_LABEL_CHARS) errors.push(`reviewer_label is longer than ${MAX_REVIEWER_LABEL_CHARS} characters`);

  if (!isIsoTimestamp(fb.created_at)) errors.push('created_at must be an ISO-8601 timestamp');
  if (!FEEDBACK_STATUSES.includes(fb.status)) errors.push(`status must be one of ${FEEDBACK_STATUSES.join('|')}`);
  if (fb.history !== undefined && !Array.isArray(fb.history)) errors.push('history must be an array');

  // Cross-checks against the project and the estimate the feedback refers to.
  if (project && isPlainObject(project)) {
    if (isNonEmptyString(fb.project_id) && fb.project_id !== project.id) {
      errors.push(`feedback belongs to project '${fb.project_id}', not '${project.id}'`);
    }
    const collection = targetType === 'measurement' ? project.measurements : targetType === 'sheet' ? project.sheets : null;
    if (collection && isNonEmptyString(target.id)) {
      const exists = Array.isArray(collection) && collection.some((x) => isPlainObject(x) && x.id === target.id);
      if (!exists) (strict ? errors : warnings).push(`target ${targetType} '${target.id}' is not in the project`);
    }
  }
  if (estimate && targetType === 'line' && isNonEmptyString(target.id)) {
    const line = findLine(estimate, target.id);
    if (!line) {
      (strict ? errors : warnings).push(`target line '${target.id}' is not in the estimate`);
    } else if (isPlainObject(suggested) && isKnownUnit(suggested.unit)) {
      const lineDim = dimensionOrNull(line.unit);
      const suggestedDim = dimensionOf(suggested.unit);
      if (lineDim === null) {
        errors.push(`suggested unit '${suggested.unit}' cannot be checked against line unit '${String(line.unit)}'`);
      } else if (lineDim !== suggestedDim) {
        errors.push(`unit mismatch: suggested '${suggested.unit}' (${suggestedDim}) vs line '${line.unit}' (${lineDim})`);
      }
    }
  }
  return { errors, warnings };
}

/**
 * Create a new open feedback record.
 * Input: {project_id, estimate_hash=null, target:{type, id}, kind, comment='',
 *         suggested={qty?, unit?}, reviewer_label=null, id?}.
 * Options: `at` (created_at, default now), `project` / `estimate` to verify the
 * target exists and the suggested unit matches the line's dimension.
 * Throws FeedbackError listing every problem.
 */
export function createFeedback(input = {}, { at, project = null, estimate = null } = {}) {
  if (!isPlainObject(input)) throw new FeedbackError('Feedback input must be an object');
  const suggested = {};
  if (input.suggested !== undefined && input.suggested !== null) {
    if (!isPlainObject(input.suggested)) throw new FeedbackError('suggested must be an object {qty?, unit?}');
    for (const [key, value] of Object.entries(input.suggested)) {
      if (value === undefined || value === null || value === '') continue;
      suggested[key] = key === 'unit' && isKnownUnit(value) ? normalizeUnit(value) : value;
    }
  }
  const target = isPlainObject(input.target)
    ? { type: input.target.type, id: input.target.type === 'general' ? null : input.target.id ?? null }
    : input.target;
  const label = input.reviewer_label === undefined || input.reviewer_label === null ? null : cleanText(input.reviewer_label);

  const fb = {
    id: isNonEmptyString(input.id) ? input.id : newId('fb'),
    app_version: APP_VERSION,
    project_id: input.project_id,
    estimate_hash: input.estimate_hash ?? null,
    target,
    kind: input.kind,
    comment: input.comment === undefined || input.comment === null ? '' : cleanText(input.comment),
    suggested,
    reviewer_label: label === '' ? null : label,
    created_at: timestamp(at),
    status: 'open',
    history: [],
  };
  const { errors } = validateFeedback(fb, { project, estimate, strict: true });
  if (errors.length) throw new FeedbackError('Invalid feedback: ' + errors[0], errors);
  return fb;
}

/**
 * Move feedback to a new status, returning a new record with a history entry.
 * Declining requires a note explaining why. Options: {at, by, note}.
 */
export function setFeedbackStatus(fb, status, { at, by = null, note = '' } = {}) {
  const { errors } = validateFeedback(fb);
  if (errors.length) throw new FeedbackError('Invalid feedback: ' + errors[0], errors);
  if (!FEEDBACK_STATUSES.includes(status)) throw new FeedbackError(`Unknown feedback status '${String(status)}'`);
  if (!STATUS_TRANSITIONS[fb.status].includes(status)) {
    throw new FeedbackError(`Cannot move feedback from '${fb.status}' to '${status}'`);
  }
  const text = note === undefined || note === null ? '' : cleanText(note);
  if (status === 'declined' && text === '') throw new FeedbackError('Declining feedback requires a note explaining why');
  const entry = {
    from: fb.status,
    to: status,
    at: timestamp(at),
    by: isNonEmptyString(by) ? cleanText(by).slice(0, MAX_REVIEWER_LABEL_CHARS) : null,
    note: text.slice(0, MAX_COMMENT_CHARS),
  };
  return { ...structuredClone(fb), status, history: [...(fb.history ?? []).map((h) => structuredClone(h)), entry] };
}

/**
 * Turn an accepted quantity correction on a line into a contractor override
 * record `{line_id, qty, reason, by, at}` for assemblies.applyOverrides.
 * `suggested.qty` is the corrected net quantity (before waste). When the
 * suggestion carries a unit, pass the target `line` so the quantity is converted
 * to the line's unit (a dimension mismatch throws).
 */
export function feedbackToOverride(fb, { line = null, by = null, at } = {}) {
  const { errors } = validateFeedback(fb);
  if (errors.length) throw new FeedbackError('Invalid feedback: ' + errors[0], errors);
  if (fb.kind !== 'quantity_correction' || fb.target.type !== 'line') {
    throw new FeedbackError('Only quantity corrections on a line can become an override');
  }
  if (fb.status !== 'accepted') throw new FeedbackError(`Feedback must be accepted first (status is '${fb.status}')`);
  if (line !== null && (!isPlainObject(line) || line.line_id !== fb.target.id)) {
    throw new FeedbackError(`Line given does not match feedback target '${fb.target.id}'`);
  }

  let qty = fb.suggested.qty;
  if (fb.suggested.unit !== undefined) {
    if (line === null) throw new FeedbackError('Pass the target line to convert the suggested unit');
    if (dimensionOrNull(line.unit) === null) {
      throw new FeedbackError(`Line unit '${String(line.unit)}' is not a unit the suggestion can be converted to`);
    }
    try {
      qty = round4(convert(qty, fb.suggested.unit, line.unit));
    } catch (err) {
      throw new FeedbackError(`Unit mismatch: ${err.message}`);
    }
  }
  const reason = (`Accepted review feedback ${fb.id}` + (fb.comment ? ': ' + fb.comment : '')).slice(0, 500);
  return {
    line_id: fb.target.id,
    qty,
    reason,
    by: isNonEmptyString(by) ? by.trim() : fb.reviewer_label,
    at: timestamp(at),
  };
}

/** Count feedback by status, kind and target type; lists line ids with open items. */
export function summarizeFeedback(feedback) {
  if (!Array.isArray(feedback)) throw new FeedbackError('feedback must be an array');
  const zero = (keys) => Object.fromEntries(keys.map((k) => [k, 0]));
  const summary = {
    total: feedback.length,
    by_status: zero(FEEDBACK_STATUSES),
    by_kind: zero(FEEDBACK_KINDS),
    by_target_type: zero(TARGET_TYPES),
    open_line_ids: [],
  };
  const openLines = new Set();
  for (const fb of feedback) {
    if (!isPlainObject(fb)) continue;
    if (fb.status in summary.by_status) summary.by_status[fb.status] += 1;
    if (fb.kind in summary.by_kind) summary.by_kind[fb.kind] += 1;
    const type = fb.target?.type;
    if (type in summary.by_target_type) summary.by_target_type[type] += 1;
    if (fb.status === 'open' && type === 'line' && isNonEmptyString(fb.target.id)) openLines.add(fb.target.id);
  }
  summary.open_line_ids = [...openLines].sort();
  return summary;
}

// ---------------------------------------------------------------------------
// Feedback packages
// ---------------------------------------------------------------------------

/** Deep copy of a value with every `attachment_ref` key removed (private quote documents). */
function withoutAttachments(value) {
  if (Array.isArray(value)) return value.map(withoutAttachments);
  if (!isPlainObject(value)) return value;
  const out = {};
  for (const [key, v] of Object.entries(value)) {
    if (key !== 'attachment_ref') out[key] = withoutAttachments(v);
  }
  return out;
}

/** Order feedback deterministically: created_at, then id. */
function byCreated(a, b) {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * Build a self-verifying feedback package for `project` (async: hashes with SHA-256).
 * Options: `at` (exported_at, default now), `estimate` (the estimate the feedback was
 * made against; its hash and totals are recorded), `include_project` (default false:
 * only a project summary + hash is included; true adds the full project with quote
 * attachment references removed). Throws FeedbackError on any invalid feedback.
 */
export async function exportFeedbackPackage(project, feedback, { at, estimate = null, include_project = false } = {}) {
  const projectCheck = validateProject(project);
  if (projectCheck.errors.length) {
    throw new FeedbackError('Cannot export feedback for an invalid project: ' + projectCheck.errors[0], projectCheck.errors);
  }
  if (!Array.isArray(feedback)) throw new FeedbackError('feedback must be an array');
  if (feedback.length > MAX_FEEDBACK_ITEMS) throw new FeedbackError(`A package can hold at most ${MAX_FEEDBACK_ITEMS} feedback items`);
  if (estimate !== null && !(isPlainObject(estimate) && Array.isArray(estimate.lines))) {
    throw new FeedbackError('estimate must be a buildEstimate result with lines');
  }

  // Canonical order before validating so input order cannot influence warning indices
  // or the self-verifying exported package hash.
  const items = feedback.map((fb) => structuredClone(fb)).sort(byCreated);
  const errors = [];
  const warnings = [];
  const ids = new Set();
  items.forEach((fb, i) => {
    const check = validateFeedback(fb, { project, estimate });
    for (const e of check.errors) errors.push(`feedback[${i}]: ${e}`);
    // Reviewer label is optional; a missing one is not a package integrity warning.
    for (const w of check.warnings) if (w !== 'reviewer_label not set') warnings.push(`feedback[${i}]: ${w}`);
    if (isPlainObject(fb) && isNonEmptyString(fb.id)) {
      if (ids.has(fb.id)) errors.push(`feedback[${i}]: duplicate id '${fb.id}'`);
      ids.add(fb.id);
    }
  });
  if (errors.length) throw new FeedbackError(`Invalid feedback (${errors.length} problem${errors.length === 1 ? '' : 's'}): ${errors[0]}`, errors);

  const estimateHash = estimate ? await snapshotHash(estimate) : null;
  if (estimateHash) {
    items.forEach((fb, i) => {
      if (fb.estimate_hash !== null && fb.estimate_hash !== estimateHash) {
        warnings.push(`feedback[${i}]: made against a different estimate snapshot (${fb.estimate_hash.slice(0, 12)}…)`);
      }
    });
  }

  const pkg = {
    schema: FEEDBACK_PACKAGE_SCHEMA,
    app_version: APP_VERSION,
    module_version: MODULE_VERSION,
    exported_at: timestamp(at),
    project: {
      id: project.id,
      name: project.name,
      scope: project.scope,
      ahj_id: project.ahj_id,
      application_date: project.application_date,
      project_hash: await snapshotHash(project),
    },
    estimate: estimate
      ? {
        estimate_hash: estimateHash,
        as_of: estimate.as_of ?? null,
        line_count: estimate.lines.length,
        totals: isPlainObject(estimate.totals) ? structuredClone(estimate.totals) : null,
      }
      : null,
    estimate_hashes: [...new Set(items.map((fb) => fb.estimate_hash).filter((h) => h !== null))].sort(),
    feedback: items,
    summary: summarizeFeedback(items),
    warnings,
    privacy: {
      contains_plan_files: false,
      contains_quote_attachments: false,
      contains_full_project: Boolean(include_project),
      note: include_project
        ? 'Full project included (measurements, catalog, quotes) with quote attachment references removed; no plan PDFs.'
        : 'Project summary and hash only; no plan PDFs, measurements, catalog or quote attachments.',
    },
  };
  if (include_project) pkg.full_project = withoutAttachments(structuredClone(project));
  pkg.package_hash = await snapshotHash(pkg);
  return pkg;
}

/**
 * Check a feedback package: schema, package_hash over its content, every feedback
 * record, project id consistency. Async; resolves to {ok, errors, warnings}.
 */
export async function verifyFeedbackPackage(pkg) {
  const errors = [];
  const warnings = [];
  if (!isPlainObject(pkg)) return { ok: false, errors: ['package must be an object'], warnings };
  if (pkg.schema !== FEEDBACK_PACKAGE_SCHEMA) errors.push(`schema must be '${FEEDBACK_PACKAGE_SCHEMA}'`);
  if (!isPlainObject(pkg.project) || !isNonEmptyString(pkg.project.id)) errors.push('project.id is required');
  if (!Array.isArray(pkg.feedback)) errors.push('feedback must be an array');
  else if (pkg.feedback.length > MAX_FEEDBACK_ITEMS) errors.push(`package holds more than ${MAX_FEEDBACK_ITEMS} feedback items`);

  if (typeof pkg.package_hash !== 'string' || !HASH_RE.test(pkg.package_hash)) {
    errors.push('package_hash is missing or malformed');
  } else {
    const { package_hash: stated, ...content } = pkg;
    let actual = null;
    try {
      actual = await snapshotHash(content);
    } catch (err) {
      errors.push('package content cannot be hashed: ' + err.message);
    }
    if (actual !== null && actual !== stated) errors.push('package_hash does not match the content (package was altered)');
  }

  if (Array.isArray(pkg.feedback) && isPlainObject(pkg.project)) {
    const ids = new Set();
    pkg.feedback.forEach((fb, i) => {
      const check = validateFeedback(fb);
      for (const e of check.errors) errors.push(`feedback[${i}]: ${e}`);
      for (const w of check.warnings) if (w !== 'reviewer_label not set') warnings.push(`feedback[${i}]: ${w}`);
      if (isPlainObject(fb)) {
        if (fb.project_id !== pkg.project.id) errors.push(`feedback[${i}]: belongs to project '${String(fb.project_id)}'`);
        if (ids.has(fb.id)) errors.push(`feedback[${i}]: duplicate id '${fb.id}'`);
        ids.add(fb.id);
      }
    });
  }
  if (isPlainObject(pkg.full_project) && JSON.stringify(pkg.full_project).includes('"attachment_ref"')) {
    errors.push('full_project contains a private quote attachment reference');
  }
  return { ok: errors.length === 0, errors, warnings };
}

/**
 * Parse feedback package JSON text (or an object) and verify it.
 * Async; resolves to the package or throws FeedbackError listing every problem.
 */
export async function parseFeedbackPackage(input) {
  let pkg;
  if (typeof input === 'string') {
    if (input.length > MAX_PACKAGE_CHARS) throw new FeedbackError('Feedback package is too large');
    try {
      pkg = JSON.parse(input.replace(/^﻿/, ''), safeReviver);
    } catch (err) {
      throw new FeedbackError('Feedback package is not valid JSON: ' + err.message);
    }
  } else if (isPlainObject(input)) {
    pkg = JSON.parse(JSON.stringify(input), safeReviver);
  } else {
    throw new FeedbackError('Feedback package must be JSON text or an object');
  }
  const { ok, errors } = await verifyFeedbackPackage(pkg);
  if (!ok) throw new FeedbackError(`Invalid feedback package: ${errors[0]}`, errors);
  return pkg;
}

/** Quote one CSV cell, neutralizing spreadsheet formula injection (= + - @ tab CR prefixes). */
function csvCell(value) {
  let s = value === null || value === undefined ? '' : String(value);
  if (/^[\s﻿]*[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replaceAll('"', '""') + '"';
}

/** Export feedback as CRLF CSV (every cell quoted, formula injection neutralized) for spreadsheet review. */
export function exportFeedbackCsv(feedback) {
  if (!Array.isArray(feedback)) throw new FeedbackError('feedback must be an array');
  if (!feedback.every(isPlainObject)) throw new FeedbackError('every feedback item must be an object');
  const header = ['Feedback ID', 'Status', 'Kind', 'Target type', 'Target ID', 'Suggested qty', 'Suggested unit',
    'Comment', 'Reviewer', 'Created at', 'Estimate hash', 'Project ID', 'App version'];
  const rows = feedback.map((fb) => [
    fb.id, fb.status, fb.kind, fb.target?.type, fb.target?.id, fb.suggested?.qty, fb.suggested?.unit,
    fb.comment, fb.reviewer_label, fb.created_at, fb.estimate_hash, fb.project_id, fb.app_version,
  ]);
  return [header, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

// ---------------------------------------------------------------------------
// Estimate comparison
// ---------------------------------------------------------------------------

/** Statuses that mean a line is deliberately outside the contractor's totals (not a missing price). */
const EXCLUDED_STATUSES = new Set(['BY_OWNER']);

/** Index the lines of an estimate (or a plain array of lines) by line_id; throws on bad or duplicate lines. */
function indexLines(estimate, label) {
  const lines = Array.isArray(estimate) ? estimate : isPlainObject(estimate) ? estimate.lines : undefined;
  if (!Array.isArray(lines)) throw new FeedbackError(`estimate ${label} must be an estimate with lines or an array of lines`);
  const index = new Map();
  lines.forEach((line, i) => {
    const at = `estimate ${label} line ${i}`;
    if (!isPlainObject(line) || !isNonEmptyString(line.line_id)) throw new FeedbackError(`${at} has no line_id`);
    if (index.has(line.line_id)) throw new FeedbackError(`${at} duplicates line_id '${line.line_id}'`);
    for (const key of ['qty', 'qty_with_waste', 'unit_cost_cents']) {
      const v = line[key];
      if (v !== undefined && v !== null && !(typeof v === 'number' && Number.isFinite(v))) {
        throw new FeedbackError(`${at} (${line.line_id}): ${key} must be a finite number or null`);
      }
    }
    const ext = line.extended_cents;
    if (ext !== undefined && ext !== null && !Number.isSafeInteger(ext)) {
      throw new FeedbackError(`${at} (${line.line_id}): extended_cents must be integer cents or null`);
    }
    index.set(line.line_id, line);
  });
  return { lines, index };
}

const numOrNull = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** How a line's cost counts: a known amount, deliberately excluded (by owner / in another line's quote), or unknown. */
function costState(line) {
  if (Number.isSafeInteger(line.extended_cents)) return { kind: 'known', cents: line.extended_cents };
  if (EXCLUDED_STATUSES.has(line.price_status)) return { kind: 'excluded', cents: null, why: 'by_owner' };
  if (line.quote_allocation === 'included') return { kind: 'excluded', cents: null, why: 'included_in_quote' };
  return { kind: 'unknown', cents: null };
}

/** Compact view of a line used for added/removed entries. */
function lineSnapshot(line) {
  return {
    line_id: line.line_id,
    description: line.description ?? null,
    material_key: line.material_key ?? null,
    unit: line.unit ?? null,
    qty: numOrNull(line.qty),
    qty_with_waste: numOrNull(line.qty_with_waste),
    extended_cents: Number.isSafeInteger(line.extended_cents) ? line.extended_cents : null,
    price_status: line.price_status ?? null,
  };
}

/** Effect of one side's cost on the known total (null when unknown). */
function costEffect(state, sign) {
  if (state.kind === 'known') return sign * state.cents;
  if (state.kind === 'excluded') return 0;
  return null;
}

/**
 * Compare quantities of one line across estimates. Units that differ are converted
 * only within the same dimension; a different dimension (or a non-unit label) makes
 * the quantity incomparable (delta null) rather than guessing.
 */
function compareQty(lineA, lineB, warnings) {
  const unitA = lineA.unit ?? null;
  const unitB = lineB.unit ?? null;
  const a = numOrNull(lineA.qty);
  const b = numOrNull(lineB.qty);
  const aw = numOrNull(lineA.qty_with_waste);
  const bw = numOrNull(lineB.qty_with_waste);
  let comparable = true;
  let factor = 1; // multiplies a quantity in unit B to express it in unit A
  let unitChanged = false;

  if (unitA !== unitB) {
    unitChanged = true;
    const dimA = typeof unitA === 'string' ? dimensionOrNull(unitA) : null;
    const dimB = typeof unitB === 'string' ? dimensionOrNull(unitB) : null;
    if (dimA !== null && dimA === dimB) {
      factor = convert(1, unitB, unitA);
      if (normalizeUnit(unitA) === normalizeUnit(unitB)) unitChanged = false; // spelling only (e.g. 'SF' vs 'sf')
    } else {
      comparable = false;
      warnings.push({
        code: dimA !== null && dimB !== null ? 'UNIT_DIMENSION_CHANGED' : 'UNIT_CHANGED_UNCOMPARABLE',
        line_id: lineA.line_id,
        message: `Unit changed from '${String(unitA)}' to '${String(unitB)}'; quantities are not compared`,
      });
    }
  }

  const delta = (x, y) => {
    if (!comparable || x === null || y === null) return null;
    return round4(y * factor - x);
  };
  const changed = (x, y) => {
    if (x === null || y === null) return x !== y;
    if (!comparable) return true;
    return !sameQty(x, y * factor);
  };
  const d = delta(a, b);
  return {
    unitChanged,
    qty: {
      a, b, unit: unitA, delta: d,
      delta_pct: d !== null && a !== 0 ? Math.round((d / a) * 10000) / 100 : null,
      comparable,
    },
    qty_with_waste: { a: aw, b: bw, delta: delta(aw, bw) },
    qtyChanged: changed(a, b),
    wasteChanged: changed(aw, bw),
  };
}

/**
 * Classify how a line's cost moved between two estimates:
 * unchanged | increase | decrease | still_unpriced | became_unpriced | became_priced | became_excluded.
 * "Excluded" means deliberately outside the contractor total (by owner, or inside another line's quote).
 */
function classifyCost(sa, sb) {
  if (sa.kind === 'known' && sb.kind === 'known') {
    if (sa.cents === sb.cents) return 'unchanged';
    return sb.cents > sa.cents ? 'increase' : 'decrease';
  }
  if (sa.kind === sb.kind) return sa.kind === 'unknown' ? 'still_unpriced' : 'unchanged';
  if (sb.kind === 'unknown') return 'became_unpriced';
  if (sb.kind === 'known') return 'became_priced';
  return 'became_excluded';
}

/** Cost classifications that are not, by themselves, a change to report. */
const COST_NO_CHANGE = new Set(['unchanged', 'still_unpriced']);

const TOTAL_KEYS = Object.freeze([
  'confirmed_cents', 'provisional_cents', 'allowance_cents', 'tax_cents', 'markup_cents', 'contingency_cents',
  'grand_total_known_cents', 'missing_lines', 'provisional_lines', 'by_owner_lines',
]);

function compareTotals(ta, tb) {
  if (!isPlainObject(ta) || !isPlainObject(tb)) return null;
  const out = {};
  for (const key of TOTAL_KEYS) {
    const a = numOrNull(ta[key]);
    const b = numOrNull(tb[key]);
    out[key] = { a, b, delta: a !== null && b !== null ? b - a : null };
  }
  out.is_complete = { a: ta.is_complete ?? null, b: tb.is_complete ?? null, changed: ta.is_complete !== tb.is_complete };
  return out;
}

/**
 * Compare two estimates (buildEstimate results or plain arrays of BOM lines), matching
 * lines by line_id. `a` is the baseline, `b` the revision; every delta is b − a.
 * Returns {added, removed, changed, unchanged_line_ids, status_changes, totals, summary, warnings}.
 * Unknown costs are never treated as $0: their effect is null and counted in
 * summary.cost_effect_unknown_lines.
 */
export function compareEstimates(a, b) {
  const A = indexLines(a, 'a');
  const B = indexLines(b, 'b');
  const warnings = [];
  const added = [];
  const removed = [];
  const changed = [];
  const unchanged = [];
  const statusChanges = [];
  let knownDelta = 0;
  let unknownEffects = 0;
  const addEffect = (effect) => {
    if (effect === null) unknownEffects += 1;
    else knownDelta += effect;
  };

  for (const lineB of B.lines) {
    const lineA = A.index.get(lineB.line_id);
    const sb = costState(lineB);
    if (!lineA) {
      const effect = costEffect(sb, +1);
      addEffect(effect);
      added.push({ ...lineSnapshot(lineB), cost_effect_cents: effect });
      continue;
    }
    const sa = costState(lineA);
    const q = compareQty(lineA, lineB, warnings);
    const costChange = classifyCost(sa, sb);
    const effect = sa.kind === 'unknown' || sb.kind === 'unknown' ? null : costEffect(sb, +1) + costEffect(sa, -1);
    const statusA = lineA.price_status ?? null;
    const statusB = lineB.price_status ?? null;
    const unitCostA = numOrNull(lineA.unit_cost_cents);
    const unitCostB = numOrNull(lineB.unit_cost_cents);

    const changes = [];
    if (q.qtyChanged) changes.push('qty');
    if (q.wasteChanged) changes.push('qty_with_waste');
    if (q.unitChanged) changes.push('unit');
    if (!COST_NO_CHANGE.has(costChange)) changes.push('cost');
    if (unitCostA !== unitCostB) changes.push('unit_cost');
    if (statusA !== statusB) changes.push('status');

    if (changes.length === 0) {
      unchanged.push(lineB.line_id);
      continue;
    }
    addEffect(effect);
    if (statusA !== statusB) statusChanges.push({ line_id: lineB.line_id, from: statusA, to: statusB });
    changed.push({
      line_id: lineB.line_id,
      description: lineB.description ?? lineA.description ?? null,
      changes,
      unit: { a: lineA.unit ?? null, b: lineB.unit ?? null, changed: q.unitChanged },
      qty: q.qty,
      qty_with_waste: q.qty_with_waste,
      cost: {
        a: sa.cents,
        b: sb.cents,
        delta: sa.kind === 'known' && sb.kind === 'known' ? sb.cents - sa.cents : null,
        change: costChange,
        effect_cents: effect,
      },
      unit_cost: { a: unitCostA, b: unitCostB, delta: unitCostA !== null && unitCostB !== null ? round4(unitCostB - unitCostA) : null },
      status: { a: statusA, b: statusB, changed: statusA !== statusB },
    });
  }

  for (const lineA of A.lines) {
    if (B.index.has(lineA.line_id)) continue;
    const effect = costEffect(costState(lineA), -1);
    addEffect(effect);
    removed.push({ ...lineSnapshot(lineA), cost_effect_cents: effect });
  }

  const totals = compareTotals(Array.isArray(a) ? null : a.totals, Array.isArray(b) ? null : b.totals);
  return {
    module_version: MODULE_VERSION,
    a: { as_of: Array.isArray(a) ? null : a.as_of ?? null, line_count: A.lines.length },
    b: { as_of: Array.isArray(b) ? null : b.as_of ?? null, line_count: B.lines.length },
    added,
    removed,
    changed,
    unchanged_line_ids: unchanged,
    status_changes: statusChanges,
    totals,
    summary: {
      added: added.length,
      removed: removed.length,
      changed: changed.length,
      unchanged: unchanged.length,
      status_changes: statusChanges.length,
      known_cost_delta_cents: knownDelta,
      cost_effect_unknown_lines: unknownEffects,
      cost_delta_is_complete: unknownEffects === 0,
    },
    warnings,
  };
}
