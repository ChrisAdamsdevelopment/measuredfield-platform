// MeasuredField Remodel Takeoff — revisions.mjs (spec §3.3)
//
// Sheet revision bookkeeping so a takeoff never double counts work that was
// re-issued on a later revision of the same drawing.
//
// Sheet record:
//   {id, file_id, page_index, sheet_number:'S0.0', title, revision:int,
//    issued_on:'YYYY-MM-DD'|null, accepted_on:'YYYY-MM-DD'|null,
//    supersedes:[sheet_id], supersession:'whole_sheet'|'clouded_only', notes}
//
// `supersedes` / `supersession` live on the NEWER sheet and describe what it
// replaces: 'whole_sheet' replaces the older sheet entirely, 'clouded_only'
// replaces only the clouded areas (the rest of the older sheet stays in force).

export const MODULE_VERSION = '0.2.0';

export const SHEET_STATUSES = Object.freeze(['effective', 'superseded', 'partially_superseded', 'conflict']);
export const SUPERSESSION_MODES = Object.freeze(['whole_sheet', 'clouded_only']);

/** Stable reason codes used in `filterMeasurementsForTakeoff().excluded[].reason`. */
export const EXCLUSION_REASONS = Object.freeze({
  SUPERSEDED: 'superseded_sheet',
  CONFLICT: 'sheet_conflict',
  UNKNOWN_SHEET: 'unknown_sheet',
  MISSING_SHEET: 'missing_sheet_id',
  REJECTED: 'rejected_in_review',
});

/** Flag reason used in `filterMeasurementsForTakeoff().flagged[].reason`. */
export const FLAG_REASONS = Object.freeze({
  PARTIALLY_SUPERSEDED: 'partially_superseded_sheet',
});

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Error thrown for invalid sheet data or supersession graphs; `code` is stable. */
export class RevisionError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'RevisionError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new RevisionError(code, message);
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isValidIsoDate(text) {
  const match = ISO_DATE.exec(text);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1) return false;
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return day <= daysInMonth;
}

/**
 * Comparison key for sheet numbers: trimmed, internal whitespace removed,
 * upper-cased ("s0.0 " and "S 0.0" both become "S0.0").
 */
export function sheetNumberKey(sheetNumber) {
  if (typeof sheetNumber !== 'string') return '';
  return sheetNumber.trim().replace(/\s+/g, '').toUpperCase();
}

/** Human label for a sheet, e.g. "S0.0 rev 1 (sheet-b1)". */
export function describeSheet(sheet) {
  return `${sheet.sheet_number} rev ${sheet.revision} (${sheet.id})`;
}

/**
 * Validate one sheet record; throws RevisionError on bad shape and returns a
 * normalized copy (`supersedes` defaults to [], `supersession` to 'whole_sheet').
 */
export function validateSheet(sheet) {
  if (!isPlainObject(sheet)) fail('BAD_SHEET', 'sheet must be an object');
  if (typeof sheet.id !== 'string' || sheet.id.trim() === '') fail('BAD_SHEET', 'sheet.id must be a non-empty string');
  const label = `sheet ${sheet.id}`;
  if (typeof sheet.sheet_number !== 'string' || sheetNumberKey(sheet.sheet_number) === '') {
    fail('BAD_SHEET', `${label}: sheet_number must be a non-empty string`);
  }
  if (!Number.isInteger(sheet.revision) || sheet.revision < 0) {
    fail('BAD_SHEET', `${label}: revision must be an integer >= 0`);
  }
  if (sheet.page_index !== undefined && sheet.page_index !== null &&
      (!Number.isInteger(sheet.page_index) || sheet.page_index < 0)) {
    fail('BAD_SHEET', `${label}: page_index must be an integer >= 0`);
  }
  for (const key of ['issued_on', 'accepted_on']) {
    const value = sheet[key];
    if (value === undefined || value === null) continue;
    if (typeof value !== 'string' || !isValidIsoDate(value)) {
      fail('BAD_DATE', `${label}: ${key} must be a real calendar date 'YYYY-MM-DD' or null`);
    }
  }
  const supersedes = sheet.supersedes ?? [];
  if (!Array.isArray(supersedes) || supersedes.some((id) => typeof id !== 'string' || id === '')) {
    fail('BAD_SHEET', `${label}: supersedes must be an array of sheet ids`);
  }
  const supersession = sheet.supersession ?? 'whole_sheet';
  if (!SUPERSESSION_MODES.includes(supersession)) {
    fail('BAD_SHEET', `${label}: supersession must be one of ${SUPERSESSION_MODES.join(', ')}`);
  }
  return { ...sheet, supersedes: [...new Set(supersedes)], supersession };
}

// Throw if the supersession graph (newer -> older) contains a cycle.
function assertAcyclic(sheets, byId) {
  const WHITE = 0;
  const GREY = 1;
  const BLACK = 2;
  const color = new Map(sheets.map((s) => [s.id, WHITE]));
  const stack = [];

  const visit = (id) => {
    color.set(id, GREY);
    stack.push(id);
    for (const olderId of byId.get(id).supersedes) {
      if (color.get(olderId) === GREY) {
        const cycle = [...stack.slice(stack.indexOf(olderId)), olderId];
        fail('SUPERSESSION_CYCLE', `supersession cycle: ${cycle.join(' -> ')}`);
      }
      if (color.get(olderId) === WHITE) visit(olderId);
    }
    stack.pop();
    color.set(id, BLACK);
  };

  for (const s of sheets) if (color.get(s.id) === WHITE) visit(s.id);
}

/**
 * Resolve the effective status of every sheet. Returns copies (input order)
 * with `{status, superseded_by, superseded_by_ids, conflicts_with, warnings}`:
 * - 'superseded' when a whole_sheet revision replaces it (directly, or via a
 *   clouded-only revision that was itself wholly replaced);
 * - 'partially_superseded' when only clouded-only revisions replace it;
 * - 'conflict' when another sheet shares its sheet_number and neither
 *   supersedes the other, directly or transitively (both are flagged);
 * - otherwise 'effective'.
 * Throws RevisionError on duplicate ids, unknown `supersedes` ids or cycles.
 */
export function resolveSheets(sheets) {
  if (!Array.isArray(sheets)) fail('BAD_SHEETS', 'sheets must be an array');
  const normalized = sheets.map(validateSheet);

  const byId = new Map();
  for (const s of normalized) {
    if (byId.has(s.id)) fail('DUPLICATE_SHEET_ID', `duplicate sheet id ${s.id}`);
    byId.set(s.id, s);
  }

  // Direct "superseded by" lists, plus link validation.
  const supersededBy = new Map(normalized.map((s) => [s.id, []]));
  for (const s of normalized) {
    for (const olderId of s.supersedes) {
      if (olderId === s.id) fail('SUPERSESSION_CYCLE', `sheet ${s.id} cannot supersede itself`);
      if (!byId.has(olderId)) fail('UNKNOWN_SHEET', `sheet ${s.id} supersedes unknown sheet id ${olderId}`);
      supersededBy.get(olderId).push(s.id);
    }
  }
  assertAcyclic(normalized, byId);

  // Base status (ignoring conflicts), memoized; the graph is acyclic.
  const base = new Map();
  const baseStatus = (id) => {
    if (base.has(id)) return base.get(id);
    const newer = supersededBy.get(id);
    let result = { status: 'effective', superseded_by: null };
    if (newer.length > 0) {
      const whole = newer.find((nid) => byId.get(nid).supersession === 'whole_sheet');
      if (whole) {
        result = { status: 'superseded', superseded_by: whole };
      } else {
        // Every direct replacement is clouded-only. If one of those revisions
        // was itself wholly replaced, its replacement covers this sheet too.
        const replacedChain = newer.find((nid) => baseStatus(nid).status === 'superseded');
        result = replacedChain
          ? { status: 'superseded', superseded_by: replacedChain }
          : { status: 'partially_superseded', superseded_by: newer[0] };
      }
    }
    base.set(id, result);
    return result;
  };

  // Transitive "older sheets reachable from X" sets, for conflict detection.
  const reach = new Map();
  const reachable = (id) => {
    if (reach.has(id)) return reach.get(id);
    const set = new Set();
    for (const olderId of byId.get(id).supersedes) {
      set.add(olderId);
      for (const deeper of reachable(olderId)) set.add(deeper);
    }
    reach.set(id, set);
    return set;
  };

  const conflicts = new Map(normalized.map((s) => [s.id, []]));
  const groups = new Map();
  for (const s of normalized) {
    const key = sheetNumberKey(s.sheet_number);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  for (const group of groups.values()) {
    for (let i = 0; i < group.length; i += 1) {
      for (let j = i + 1; j < group.length; j += 1) {
        const a = group[i];
        const b = group[j];
        if (!reachable(a.id).has(b.id) && !reachable(b.id).has(a.id)) {
          conflicts.get(a.id).push(b.id);
          conflicts.get(b.id).push(a.id);
        }
      }
    }
  }

  return normalized.map((s) => {
    const { status, superseded_by } = baseStatus(s.id);
    const warnings = [];
    for (const olderId of s.supersedes) {
      const older = byId.get(olderId);
      if (sheetNumberKey(older.sheet_number) !== sheetNumberKey(s.sheet_number)) {
        warnings.push({
          code: 'SUPERSEDES_DIFFERENT_NUMBER',
          message: `${describeSheet(s)} supersedes ${describeSheet(older)} which has a different sheet number; confirm the renumbering`,
        });
      } else if (older.revision >= s.revision) {
        warnings.push({
          code: 'REVISION_NOT_NEWER',
          message: `${describeSheet(s)} supersedes ${describeSheet(older)} but its revision number is not higher`,
        });
      }
      if (s.issued_on && older.issued_on && s.issued_on < older.issued_on) {
        warnings.push({
          code: 'ISSUED_BEFORE_SUPERSEDED',
          message: `${describeSheet(s)} (issued ${s.issued_on}) supersedes ${describeSheet(older)} issued later (${older.issued_on})`,
        });
      }
    }
    const conflictsWith = conflicts.get(s.id);
    if (conflictsWith.length > 0) {
      warnings.push({
        code: 'SHEET_NUMBER_CONFLICT',
        message: `${describeSheet(s)} shares its sheet number with ${conflictsWith.join(', ')} and neither supersedes the other; link the revisions or remove the duplicate`,
      });
    }
    return {
      ...s,
      status: conflictsWith.length > 0 ? 'conflict' : status,
      superseded_by,
      superseded_by_ids: [...supersededBy.get(s.id)],
      conflicts_with: [...conflictsWith],
      warnings,
    };
  });
}

// Accept either resolved sheets or raw sheet records.
function ensureResolved(sheets) {
  if (!Array.isArray(sheets)) fail('BAD_SHEETS', 'resolvedSheets must be an array');
  const resolved = sheets.every((s) => isPlainObject(s) && SHEET_STATUSES.includes(s.status));
  return resolved ? sheets : resolveSheets(sheets);
}

/**
 * Split measurements into those that may feed the takeoff and those that may
 * not. Returns `{included, excluded:[{measurement_id, sheet_id, reason, detail}],
 * flagged:[{measurement_id, sheet_id, reason, detail}]}`.
 * - sheet superseded (whole sheet) → excluded;
 * - sheet in conflict → excluded until the user links or removes a duplicate;
 * - sheet unknown / missing, or measurement review 'rejected' → excluded;
 * - sheet partially superseded (clouded-only) → included, copy carries
 *   `needs_revision_review:true` and is listed in `flagged`.
 * Included items are shallow copies with `needs_revision_review` set.
 */
export function filterMeasurementsForTakeoff(measurements, resolvedSheets) {
  if (!Array.isArray(measurements)) fail('BAD_MEASUREMENTS', 'measurements must be an array');
  const sheets = ensureResolved(resolvedSheets);
  const byId = new Map(sheets.map((s) => [s.id, s]));

  const included = [];
  const excluded = [];
  const flagged = [];

  for (const m of measurements) {
    if (!isPlainObject(m) || typeof m.id !== 'string' || m.id === '') {
      fail('BAD_MEASUREMENTS', 'every measurement needs a non-empty string id');
    }
    const exclude = (reason, detail) => excluded.push({ measurement_id: m.id, sheet_id: m.sheet_id ?? null, reason, detail });

    if (typeof m.sheet_id !== 'string' || m.sheet_id === '') {
      exclude(EXCLUSION_REASONS.MISSING_SHEET, `measurement ${m.id} is not attached to a sheet`);
      continue;
    }
    const sheet = byId.get(m.sheet_id);
    if (!sheet) {
      exclude(EXCLUSION_REASONS.UNKNOWN_SHEET, `measurement ${m.id} references unknown sheet ${m.sheet_id}`);
      continue;
    }
    if (m.review === 'rejected') {
      exclude(EXCLUSION_REASONS.REJECTED, `measurement ${m.id} was rejected in review`);
      continue;
    }
    if (sheet.status === 'conflict') {
      exclude(
        EXCLUSION_REASONS.CONFLICT,
        `${describeSheet(sheet)} conflicts with ${(sheet.conflicts_with ?? []).join(', ')} (same sheet number, no supersession link); excluded until resolved`,
      );
      continue;
    }
    if (sheet.status === 'superseded') {
      const newer = byId.get(sheet.superseded_by);
      const by = newer ? describeSheet(newer) : sheet.superseded_by;
      exclude(
        EXCLUSION_REASONS.SUPERSEDED,
        `${describeSheet(sheet)} is superseded by ${by}; trace this item on the effective revision instead`,
      );
      continue;
    }
    const needsReview = sheet.status === 'partially_superseded';
    if (needsReview) {
      const newer = byId.get(sheet.superseded_by);
      flagged.push({
        measurement_id: m.id,
        sheet_id: m.sheet_id,
        reason: FLAG_REASONS.PARTIALLY_SUPERSEDED,
        detail: `${describeSheet(sheet)} is partially superseded (clouded areas) by ${newer ? describeSheet(newer) : sheet.superseded_by}; confirm this item lies outside the revised clouds`,
      });
    }
    included.push({ ...m, needs_revision_review: needsReview });
  }

  return { included, excluded, flagged };
}

// Union-find over sheets: same sheet number OR a supersession link => one family.
function revisionFamilies(sheets) {
  const parent = new Map(sheets.map((s) => [s.id, s.id]));
  const find = (id) => {
    let root = id;
    while (parent.get(root) !== root) root = parent.get(root);
    parent.set(id, root);
    return root;
  };
  const union = (a, b) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(rb, ra);
  };
  const firstByNumber = new Map();
  for (const s of sheets) {
    const key = sheetNumberKey(s.sheet_number);
    if (firstByNumber.has(key)) union(firstByNumber.get(key), s.id);
    else firstByNumber.set(key, s.id);
    for (const olderId of s.supersedes ?? []) if (parent.has(olderId)) union(s.id, olderId);
  }
  return find;
}

/**
 * Warn when the same `material_key` is derived from measurements on two or
 * more revisions of the same sheet (same sheet_number, or linked by
 * supersession). A line's sheets come from `line.sheet_ids`, `line.sheet_id`,
 * or — when the optional `measurements` array is given — from the sheets of
 * its `measurement_ids`. Returns `[{code:'DUPLICATE_ACROSS_REVISIONS',
 * material_key, sheet_numbers, sheet_ids, revisions, line_ids, message}]`.
 */
export function detectDuplicateItems(bomLines, resolvedSheets, measurements = []) {
  if (!Array.isArray(bomLines)) fail('BAD_LINES', 'bomLines must be an array');
  const sheets = ensureResolved(resolvedSheets);
  const sheetById = new Map(sheets.map((s) => [s.id, s]));
  const familyOf = revisionFamilies(sheets);
  const measurementSheet = new Map();
  for (const m of Array.isArray(measurements) ? measurements : []) {
    if (isPlainObject(m) && m.id) measurementSheet.set(m.id, m.sheet_id);
  }

  const lineSheets = (line) => {
    if (Array.isArray(line.sheet_ids)) return line.sheet_ids;
    if (typeof line.sheet_id === 'string') return [line.sheet_id];
    return (line.measurement_ids ?? []).map((id) => measurementSheet.get(id)).filter(Boolean);
  };

  // material_key -> family root -> {sheetIds:Set, lineIds:Set}
  const groups = new Map();
  for (const line of bomLines) {
    if (!isPlainObject(line) || !line.material_key) continue;
    for (const sheetId of new Set(lineSheets(line))) {
      if (!sheetById.has(sheetId)) continue;
      const family = familyOf(sheetId);
      const key = `${line.material_key}\u0000${family}`;
      if (!groups.has(key)) groups.set(key, { material_key: line.material_key, sheetIds: new Set(), lineIds: new Set() });
      const g = groups.get(key);
      g.sheetIds.add(sheetId);
      if (line.line_id) g.lineIds.add(line.line_id);
    }
  }

  const warnings = [];
  for (const g of groups.values()) {
    if (g.sheetIds.size < 2) continue;
    const involved = [...g.sheetIds].map((id) => sheetById.get(id)).sort((a, b) => a.revision - b.revision);
    const numbers = [...new Set(involved.map((s) => s.sheet_number))];
    warnings.push({
      code: 'DUPLICATE_ACROSS_REVISIONS',
      material_key: g.material_key,
      sheet_numbers: numbers,
      sheet_ids: involved.map((s) => s.id),
      revisions: involved.map((s) => s.revision),
      line_ids: [...g.lineIds],
      message: `${g.material_key} is taken off from ${involved.map(describeSheet).join(' and ')}; the same work may be counted twice`,
    });
  }
  return warnings;
}
