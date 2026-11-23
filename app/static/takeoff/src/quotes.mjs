// MeasuredField Remodel Takeoff — supplier quote lifecycle (architecture v2, section 3.8).
//
// A quote covers one or more BOM lines (custom cabinets, glass, engineered members,
// specialty tile ...). Until a real written vendor price is received the lines stay
// QUOTE_PENDING; a missing quote is never shown or totalled as $0.00.
//
// State machine (stored statuses):
//
//   QUOTE_REQUIRED --request--> QUOTE_REQUESTED --receive--> QUOTE_RECEIVED
//   QUOTE_RECEIVED --review--> REVIEWED --approve--> APPROVED
//   QUOTE_RECEIVED | REVIEWED --reject--> QUOTE_REQUIRED   (prior offer kept in history)
//   EXPIRED --request--> QUOTE_REQUESTED                   (re-request)
//
// EXPIRED is *derived*: a RECEIVED / REVIEWED / APPROVED quote whose `valid_until`
// calendar date is before the as-of date is EXPIRED. There is no implicit "now":
// every function that needs a date takes it as an argument.
//
// All functions are pure: `transition` returns a new quote object and never
// mutates its input.

export const MODULE_VERSION = '0.2.0';

/** Quote statuses. EXPIRED is normally derived by `effectiveStatus`, not stored. */
export const QUOTE_STATUS = Object.freeze({
  QUOTE_REQUIRED: 'QUOTE_REQUIRED',
  QUOTE_REQUESTED: 'QUOTE_REQUESTED',
  QUOTE_RECEIVED: 'QUOTE_RECEIVED',
  REVIEWED: 'REVIEWED',
  APPROVED: 'APPROVED',
  EXPIRED: 'EXPIRED',
});

const ALL_STATUSES = new Set(Object.values(QUOTE_STATUS));

/** Statuses that carry a vendor offer (amount + validity) and can therefore expire. */
const OFFER_STATUSES = new Set([
  QUOTE_STATUS.QUOTE_RECEIVED,
  QUOTE_STATUS.REVIEWED,
  QUOTE_STATUS.APPROVED,
]);

/**
 * Allowed transitions, keyed by action. `from` lists *effective* statuses
 * (i.e. after expiry has been derived for the transition timestamp).
 */
const TRANSITIONS = Object.freeze({
  request: Object.freeze({ from: [QUOTE_STATUS.QUOTE_REQUIRED, QUOTE_STATUS.EXPIRED], to: QUOTE_STATUS.QUOTE_REQUESTED }),
  receive: Object.freeze({ from: [QUOTE_STATUS.QUOTE_REQUESTED], to: QUOTE_STATUS.QUOTE_RECEIVED }),
  review: Object.freeze({ from: [QUOTE_STATUS.QUOTE_RECEIVED], to: QUOTE_STATUS.REVIEWED }),
  approve: Object.freeze({ from: [QUOTE_STATUS.REVIEWED], to: QUOTE_STATUS.APPROVED }),
  reject: Object.freeze({ from: [QUOTE_STATUS.QUOTE_RECEIVED, QUOTE_STATUS.REVIEWED], to: QUOTE_STATUS.QUOTE_REQUIRED }),
});

/** Names of the actions accepted by `transition`. */
export const QUOTE_ACTIONS = Object.freeze(Object.keys(TRANSITIONS));

/** Error thrown for invalid quotes, payloads, dates or transitions. `code` is machine-readable. */
export class QuoteError extends Error {
  constructor(message, code = 'INVALID_QUOTE', details = {}) {
    super(message);
    this.name = 'QuoteError';
    this.code = code;
    this.details = details;
  }
}

// ---------------------------------------------------------------------------
// Date helpers (no implicit "now")
// ---------------------------------------------------------------------------

const DATE_PREFIX = /^(\d{4})-(\d{2})-(\d{2})(T.+)?$/;

/**
 * Normalize a date-like value to a 'YYYY-MM-DD' calendar date.
 * Accepts 'YYYY-MM-DD', an ISO 8601 date-time string (the calendar date *as written*
 * is used, so '2026-12-01T23:00:00-08:00' is 2026-12-01) or a Date (UTC date).
 * Throws QuoteError(code 'INVALID_DATE') for anything else, including impossible
 * dates such as 2026-02-30.
 */
export function toCalendarDate(value, label = 'date') {
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) {
      throw new QuoteError(`${label} is an invalid Date`, 'INVALID_DATE', { label });
    }
    return value.toISOString().slice(0, 10);
  }
  if (typeof value !== 'string' || value.trim() === '') {
    throw new QuoteError(`${label} is required (YYYY-MM-DD or ISO 8601 date-time)`, 'INVALID_DATE', { label });
  }
  const text = value.trim();
  const match = DATE_PREFIX.exec(text);
  if (!match) {
    throw new QuoteError(`${label} must be YYYY-MM-DD or ISO 8601, got "${value}"`, 'INVALID_DATE', { label });
  }
  const [, y, m, d, time] = match;
  const year = Number(y);
  const month = Number(m);
  const day = Number(d);
  const daysInMonth = month >= 1 && month <= 12 ? new Date(Date.UTC(year, month, 0)).getUTCDate() : 0;
  if (day < 1 || day > daysInMonth) {
    throw new QuoteError(`${label} is not a real calendar date: "${value}"`, 'INVALID_DATE', { label });
  }
  if (time && Number.isNaN(Date.parse(text))) {
    throw new QuoteError(`${label} has an invalid time part: "${value}"`, 'INVALID_DATE', { label });
  }
  return `${y}-${m}-${d}`;
}

/** Keep the timestamp as the caller wrote it (string) after validating it; Dates become ISO strings. */
function normalizeTimestamp(at, label) {
  toCalendarDate(at, label);
  return at instanceof Date ? at.toISOString() : at.trim();
}

// ---------------------------------------------------------------------------
// Field validation helpers
// ---------------------------------------------------------------------------

function optionalText(value, label) {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string') {
    throw new QuoteError(`${label} must be a string or null`, 'INVALID_PAYLOAD', { field: label });
  }
  const text = value.trim();
  return text === '' ? null : text;
}

function requiredText(value, label, code = 'INVALID_PAYLOAD') {
  const text = optionalText(value, label);
  if (text === null) {
    throw new QuoteError(`${label} is required`, code, { field: label });
  }
  return text;
}

function validateLineIds(lineIds) {
  if (!Array.isArray(lineIds) || lineIds.length === 0) {
    throw new QuoteError('line_ids must be a non-empty array of BOM line ids', 'INVALID_QUOTE', { field: 'line_ids' });
  }
  const seen = new Set();
  for (const id of lineIds) {
    if (typeof id !== 'string' || id.trim() === '') {
      throw new QuoteError('every line_id must be a non-empty string', 'INVALID_QUOTE', { field: 'line_ids' });
    }
    if (seen.has(id)) {
      throw new QuoteError(`duplicate line_id "${id}" in quote`, 'INVALID_QUOTE', { field: 'line_ids' });
    }
    seen.add(id);
  }
  return [...lineIds];
}

function assertQuoteShape(quote) {
  if (!quote || typeof quote !== 'object') {
    throw new QuoteError('quote must be an object', 'INVALID_QUOTE');
  }
  if (!ALL_STATUSES.has(quote.status)) {
    throw new QuoteError(`unknown quote status "${quote.status}"`, 'INVALID_QUOTE', { status: quote.status });
  }
  if (!Array.isArray(quote.line_ids)) {
    throw new QuoteError('quote.line_ids must be an array', 'INVALID_QUOTE');
  }
  if (quote.history !== undefined && !Array.isArray(quote.history)) {
    throw new QuoteError('quote.history must be an array', 'INVALID_QUOTE');
  }
}

function generateQuoteId() {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
    return `quote_${cryptoApi.randomUUID()}`;
  }
  return `quote_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

/** Shallow copy with fresh arrays so that callers can never mutate a previous version. */
function cloneQuote(quote) {
  return {
    ...quote,
    line_ids: [...quote.line_ids],
    exclusions: Array.isArray(quote.exclusions) ? [...quote.exclusions] : [],
    history: Array.isArray(quote.history) ? quote.history.map((entry) => ({ ...entry })) : [],
  };
}

/** Snapshot of the vendor offer kept in history when it is cleared (reject / re-request). */
function offerSnapshot(quote) {
  return {
    vendor_name: quote.vendor_name ?? null,
    amount_cents: quote.amount_cents ?? null,
    valid_until: quote.valid_until ?? null,
    lead_time_days: quote.lead_time_days ?? null,
    received_at: quote.received_at ?? null,
    reviewed_by: quote.reviewed_by ?? null,
    approved_by: quote.approved_by ?? null,
    exclusions: Array.isArray(quote.exclusions) ? [...quote.exclusions] : [],
    attachment_ref: quote.attachment_ref ?? null,
  };
}

function clearOffer(quote) {
  quote.vendor_name = null;
  quote.amount_cents = null;
  quote.valid_until = null;
  quote.lead_time_days = null;
  quote.received_at = null;
  quote.reviewed_by = null;
  quote.reviewed_at = null;
  quote.approved_by = null;
  quote.approved_at = null;
  quote.exclusions = [];
  quote.attachment_ref = null;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Create a new quote in status QUOTE_REQUIRED for the given BOM lines.
 * `line_ids` (non-empty, unique) and `scope` (non-empty text) are required.
 * Optional: `id` (generated when omitted), `vendor_name`, `created_at`.
 */
export function createQuote({ line_ids, scope, id, vendor_name = null, created_at = null } = {}) {
  const lineIds = validateLineIds(line_ids);
  const scopeText = requiredText(scope, 'scope', 'INVALID_QUOTE');
  const quoteId = id === undefined || id === null ? generateQuoteId() : requiredText(id, 'id', 'INVALID_QUOTE');
  return {
    id: quoteId,
    line_ids: lineIds,
    vendor_name: optionalText(vendor_name, 'vendor_name'),
    scope: scopeText,
    status: QUOTE_STATUS.QUOTE_REQUIRED,
    amount_cents: null,
    valid_until: null,
    lead_time_days: null,
    exclusions: [],
    created_at: created_at === null || created_at === undefined ? null : normalizeTimestamp(created_at, 'created_at'),
    requested_at: null,
    received_at: null,
    reviewed_by: null,
    reviewed_at: null,
    approved_by: null,
    approved_at: null,
    attachment_ref: null,
    history: [],
  };
}

/**
 * Status of a quote as of a given date. `asOfDate` is required (no implicit now).
 * RECEIVED / REVIEWED / APPROVED quotes whose valid_until is before asOfDate are 'EXPIRED';
 * a quote is still valid on its valid_until date itself.
 */
export function effectiveStatus(quote, asOfDate) {
  assertQuoteShape(quote);
  if (asOfDate === undefined || asOfDate === null) {
    throw new QuoteError('effectiveStatus requires an explicit asOfDate (no implicit now)', 'AS_OF_REQUIRED');
  }
  const asOfDay = toCalendarDate(asOfDate, 'asOfDate');
  if (OFFER_STATUSES.has(quote.status) && quote.valid_until !== null && quote.valid_until !== undefined) {
    const validUntil = toCalendarDate(quote.valid_until, 'valid_until');
    if (validUntil < asOfDay) return QUOTE_STATUS.EXPIRED;
  }
  return quote.status;
}

/** Actions that `transition` would accept for this quote at time `at`. */
export function allowedActions(quote, at) {
  const current = effectiveStatus(quote, at);
  return QUOTE_ACTIONS.filter((action) => TRANSITIONS[action].from.includes(current));
}

/**
 * Apply a lifecycle action and return a NEW quote (input is not mutated).
 *
 * Actions and payloads:
 * - 'request'  QUOTE_REQUIRED | EXPIRED → QUOTE_REQUESTED  {vendor_name?, by?, note?}
 *              (from EXPIRED the old offer is cleared and kept in history `prior`)
 * - 'receive'  QUOTE_REQUESTED → QUOTE_RECEIVED  {amount_cents (int ≥ 0), valid_until (date ≥ at),
 *              lead_time_days? (int ≥ 0), vendor_name?, exclusions? [text], attachment_ref?, by?, note?}
 * - 'review'   QUOTE_RECEIVED → REVIEWED   {reviewed_by | by (required), note?}
 * - 'approve'  REVIEWED → APPROVED          {approved_by | by (required), note?}
 * - 'reject'   QUOTE_RECEIVED | REVIEWED → QUOTE_REQUIRED  {by?, note? | reason?}
 *              (offer cleared and kept in history `prior`)
 *
 * `at` (ISO timestamp or YYYY-MM-DD) is required; expiry is evaluated as of `at`,
 * so an expired quote cannot be reviewed, approved or rejected — only re-requested.
 * Throws QuoteError with code 'INVALID_TRANSITION', 'INVALID_PAYLOAD' or 'INVALID_DATE'.
 */
export function transition(quote, action, payload, at) {
  assertQuoteShape(quote);
  const rule = TRANSITIONS[action];
  if (!rule) {
    throw new QuoteError(`unknown quote action "${action}"`, 'INVALID_TRANSITION', { action });
  }
  if (at === undefined || at === null) {
    throw new QuoteError('transition requires an explicit timestamp `at`', 'INVALID_DATE', { field: 'at' });
  }
  const timestamp = normalizeTimestamp(at, 'at');
  const current = effectiveStatus(quote, timestamp);
  if (!rule.from.includes(current)) {
    throw new QuoteError(
      `cannot "${action}" a quote in status ${current} (allowed from: ${rule.from.join(', ')})`,
      'INVALID_TRANSITION',
      { action, from: current },
    );
  }

  const input = payload ?? {};
  if (typeof input !== 'object') {
    throw new QuoteError('payload must be an object', 'INVALID_PAYLOAD');
  }
  const by = optionalText(input.by, 'by');
  const note = optionalText(input.note ?? input.reason, 'note');
  const next = cloneQuote(quote);
  const entry = { action, from: current, to: rule.to, at: timestamp, by, note };

  switch (action) {
    case 'request': {
      if (current === QUOTE_STATUS.EXPIRED) {
        entry.prior = offerSnapshot(quote);
        clearOffer(next);
      }
      if (input.vendor_name !== undefined) next.vendor_name = optionalText(input.vendor_name, 'vendor_name');
      next.requested_at = timestamp;
      break;
    }
    case 'receive': {
      const amount = input.amount_cents;
      if (!Number.isSafeInteger(amount) || amount < 0) {
        throw new QuoteError('amount_cents must be an integer number of cents ≥ 0', 'INVALID_PAYLOAD', { field: 'amount_cents' });
      }
      if (input.valid_until === undefined || input.valid_until === null) {
        throw new QuoteError('valid_until is required to receive a quote', 'INVALID_PAYLOAD', { field: 'valid_until' });
      }
      let validUntil;
      try {
        validUntil = toCalendarDate(input.valid_until, 'valid_until');
      } catch (err) {
        throw new QuoteError(err.message, 'INVALID_PAYLOAD', { field: 'valid_until' });
      }
      if (validUntil < toCalendarDate(timestamp, 'at')) {
        throw new QuoteError(
          `valid_until ${validUntil} is before the receive date; an already-expired offer cannot be received`,
          'INVALID_PAYLOAD',
          { field: 'valid_until' },
        );
      }
      const leadTime = input.lead_time_days;
      if (leadTime !== undefined && leadTime !== null && (!Number.isSafeInteger(leadTime) || leadTime < 0)) {
        throw new QuoteError('lead_time_days must be an integer ≥ 0 or null', 'INVALID_PAYLOAD', { field: 'lead_time_days' });
      }
      if (input.exclusions !== undefined && !(Array.isArray(input.exclusions) && input.exclusions.every((x) => typeof x === 'string'))) {
        throw new QuoteError('exclusions must be an array of strings', 'INVALID_PAYLOAD', { field: 'exclusions' });
      }
      next.amount_cents = amount;
      next.valid_until = validUntil;
      next.lead_time_days = leadTime ?? null;
      if (input.vendor_name !== undefined) next.vendor_name = optionalText(input.vendor_name, 'vendor_name');
      if (input.exclusions !== undefined) next.exclusions = input.exclusions.map((x) => x.trim()).filter(Boolean);
      if (input.attachment_ref !== undefined) next.attachment_ref = optionalText(input.attachment_ref, 'attachment_ref');
      next.received_at = timestamp;
      break;
    }
    case 'review': {
      const reviewer = requiredText(input.reviewed_by ?? input.by, 'reviewed_by');
      next.reviewed_by = reviewer;
      next.reviewed_at = timestamp;
      entry.by = reviewer;
      break;
    }
    case 'approve': {
      const approver = requiredText(input.approved_by ?? input.by, 'approved_by');
      next.approved_by = approver;
      next.approved_at = timestamp;
      entry.by = approver;
      break;
    }
    case 'reject': {
      entry.prior = offerSnapshot(quote);
      clearOffer(next);
      next.requested_at = null;
      break;
    }
    default:
      // Unreachable: guarded by TRANSITIONS lookup above.
      throw new QuoteError(`unknown quote action "${action}"`, 'INVALID_TRANSITION', { action });
  }

  next.status = rule.to;
  next.history.push(entry);
  return next;
}

/**
 * How a quote contributes to estimate pricing as of `asOf` (required):
 * - APPROVED and not expired → bucket 'confirmed', price_status 'QUOTE_APPROVED'
 * - RECEIVED / REVIEWED, not expired → bucket 'provisional', price_status 'QUOTE_PROVISIONAL'
 * - anything else (required, requested, expired, malformed offer) → bucket null,
 *   amount_cents null, price_status 'QUOTE_PENDING' and a `reason`.
 */
export function quoteContribution(quote, asOf) {
  const status = effectiveStatus(quote, asOf);
  const pending = (reason) => ({
    quote_id: quote.id ?? null,
    effective_status: status,
    bucket: null,
    price_status: 'QUOTE_PENDING',
    amount_cents: null,
    reason,
  });

  if (status === QUOTE_STATUS.EXPIRED) return pending('QUOTE_EXPIRED');
  if (!OFFER_STATUSES.has(status)) return pending('QUOTE_NOT_RECEIVED');
  if (!Number.isSafeInteger(quote.amount_cents) || quote.amount_cents < 0) return pending('QUOTE_AMOUNT_INVALID');
  if (quote.valid_until === null || quote.valid_until === undefined) return pending('QUOTE_VALIDITY_UNKNOWN');

  const approved = status === QUOTE_STATUS.APPROVED;
  return {
    quote_id: quote.id ?? null,
    effective_status: status,
    bucket: approved ? 'confirmed' : 'provisional',
    price_status: approved ? 'QUOTE_APPROVED' : 'QUOTE_PROVISIONAL',
    amount_cents: quote.amount_cents,
    reason: null,
  };
}

/**
 * Copy of a quote that is safe for public export: `attachment_ref` (a private
 * document reference) is removed from the quote and from every history snapshot.
 */
export function toPublicQuote(quote) {
  assertQuoteShape(quote);
  const { attachment_ref: _private, ...rest } = cloneQuote(quote);
  rest.history = rest.history.map((entry) => {
    if (!entry.prior) return entry;
    const { attachment_ref: _priorPrivate, ...prior } = entry.prior;
    return { ...entry, prior };
  });
  return rest;
}
