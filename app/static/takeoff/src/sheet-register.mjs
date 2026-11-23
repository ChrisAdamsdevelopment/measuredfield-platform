/**
 * sheet-register.mjs — page-level drawing register for revision safety.
 *
 * One record per PHYSICAL PDF page (a page may hold several drawing views; views on
 * the same page are never revisions of each other). Records start from printed-text
 * suggestions and are confirmed or corrected by the contractor. Supersession is only
 * ever created by an explicit contractor decision — never from dates or stamps alone.
 *
 * Resolution and measurement exclusion reuse revisions.mjs (resolveSheets,
 * filterMeasurementsForTakeoff) unchanged.
 */
import { resolveSheets, filterMeasurementsForTakeoff } from './revisions.mjs';

export const MODULE_VERSION = '0.3.0';

export const pageRecordId = (fileId, pageIndex) => `pg:${fileId}:${pageIndex}`;
const normNumber = n => String(n || '').toUpperCase().replace(/\s+/g, '');

/**
 * Add a record for every page of every document; keep contractor edits; drop pages of
 * removed documents. Suggestions fill only fields the contractor has not set.
 * @param {object[]} existing
 * @param {{id:string,name:string,pageCount:number,identities?:object[]}[]} documents
 */
export function syncRegister(existing, documents) {
  const byId = new Map((existing || []).map(r => [r.id, r]));
  const out = [];
  for (const doc of documents || []) {
    for (let i = 0; i < doc.pageCount; i++) {
      const id = pageRecordId(doc.id, i);
      const ident = (doc.identities || []).find(x => x.page_index === i) || null;
      const prev = byId.get(id);
      const suggested = {
        sheet_number: ident?.sheet_number || null,
        revision: Number.isInteger(ident?.revision) ? ident.revision : null,
        issued_on: ident?.issued_on || null,
        accepted_on: ident?.accepted_on || null,
        evidence: ident?.sheet_number_candidates?.[0]?.evidence || null,
      };
      if (prev) { out.push({ ...prev, suggested, file_name: doc.name }); continue; }
      out.push({
        id, file_id: doc.id, file_name: doc.name, page_index: i,
        sheet_number: suggested.sheet_number, revision: suggested.revision ?? 0,
        issued_on: suggested.issued_on, accepted_on: suggested.accepted_on,
        supersedes: [], supersession: 'whole_sheet', distinct_from: [],
        confirmed: false, confirmed_at: null, suggested,
      });
    }
  }
  // Supersession links to pages that no longer exist are dropped.
  const ids = new Set(out.map(r => r.id));
  return out.map(r => ({ ...r, supersedes: (r.supersedes || []).filter(x => ids.has(x)), distinct_from: (r.distinct_from || []).filter(x => ids.has(x)) }));
}

/** Human label for a page record. */
export function pageLabel(r) {
  return `Page ${r.page_index + 1}${r.file_name ? ' of ' + r.file_name : ''}`;
}

/** Records converted for revisions.mjs. Unnumbered or explicitly distinct sheets get a unique number. */
export function toRevisionRecords(records) {
  return records.map(r => {
    const number = normNumber(r.sheet_number);
    const unique = !number || (r.distinct_from || []).length ? `${number || 'UNNUMBERED'}#${r.id}` : number;
    return {
      id: r.id, file_id: r.file_id, page_index: r.page_index, sheet_number: unique,
      title: pageLabel(r), revision: Number.isInteger(r.revision) ? r.revision : 0,
      issued_on: r.issued_on || null, accepted_on: r.accepted_on || null,
      supersedes: [...(r.supersedes || [])], supersession: r.supersession || 'whole_sheet',
      notes: r.confirmed ? 'Confirmed by contractor' : 'Suggested from printed text',
    };
  });
}

/** Groups of pages that share a sheet number with no supersession link and no "different sheets" decision. */
export function duplicateGroups(records) {
  const groups = new Map();
  for (const r of records) {
    const n = normNumber(r.sheet_number);
    if (!n) continue;
    if (!groups.has(n)) groups.set(n, []);
    groups.get(n).push(r);
  }
  const linked = (a, b) => (a.supersedes || []).includes(b.id) || (b.supersedes || []).includes(a.id) ||
    (a.distinct_from || []).includes(b.id) || (b.distinct_from || []).includes(a.id);
  const out = [];
  for (const [number, list] of groups) {
    if (list.length < 2) continue;
    const unresolved = list.filter(a => list.some(b => b !== a && !linked(a, b)));
    if (unresolved.length) out.push({ sheet_number: list[0].sheet_number, records: list, suggested_current: suggestCurrent(list) });
  }
  return out;
}

/** A SUGGESTION only (higher revision, then later date). The contractor decides. */
function suggestCurrent(list) {
  return [...list].sort((a, b) => (b.revision ?? 0) - (a.revision ?? 0) || String(b.issued_on || '').localeCompare(String(a.issued_on || '')))[0].id;
}

const touch = (r, at) => ({ ...r, confirmed: true, confirmed_at: at });

/** Contractor edits identity fields (sheet number, revision, dates) and confirms them. */
export function confirmIdentity(records, id, fields, at) {
  if (typeof at !== 'string' || !at) throw new TypeError('timestamp required');
  const allowed = {};
  if ('sheet_number' in fields) allowed.sheet_number = String(fields.sheet_number || '').trim().slice(0, 24) || null;
  if ('revision' in fields) {
    const n = Number(fields.revision);
    if (!Number.isInteger(n) || n < 0 || n > 99) throw new RangeError('revision must be a whole number 0-99');
    allowed.revision = n;
  }
  for (const k of ['issued_on', 'accepted_on']) {
    if (k in fields) {
      const v = fields[k] || null;
      if (v !== null && !/^\d{4}-\d{2}-\d{2}$/.test(v)) throw new RangeError(k + ' must be YYYY-MM-DD');
      allowed[k] = v;
    }
  }
  if (!records.some(r => r.id === id)) throw new RangeError('unknown page record');
  return records.map(r => (r.id === id ? touch({ ...r, ...allowed }, at) : r));
}

/**
 * Contractor decision: `currentId` replaces `replacedId`, wholly or in clouded areas only.
 * Any previous decision between the two pages is replaced.
 */
export function linkSupersession(records, { currentId, replacedId, mode, at }) {
  if (!['whole_sheet', 'clouded_only'].includes(mode)) throw new RangeError('mode must be whole_sheet or clouded_only');
  if (typeof at !== 'string' || !at) throw new TypeError('timestamp required');
  if (currentId === replacedId) throw new RangeError('a sheet cannot replace itself');
  const cur = records.find(r => r.id === currentId), old = records.find(r => r.id === replacedId);
  if (!cur || !old) throw new RangeError('unknown page record');
  const cleared = clearDecision(records, currentId, replacedId);
  return cleared.map(r => {
    if (r.id === currentId) return touch({ ...r, supersedes: [...new Set([...(r.supersedes || []), replacedId])], supersession: mode }, at);
    if (r.id === replacedId) return touch(r, at);
    return r;
  });
}

/** Contractor decision: same printed number but different sheets (both stay in the takeoff). */
export function markDistinct(records, idA, idB, at) {
  if (typeof at !== 'string' || !at) throw new TypeError('timestamp required');
  const cleared = clearDecision(records, idA, idB);
  return cleared.map(r => {
    if (r.id === idA) return touch({ ...r, distinct_from: [...new Set([...(r.distinct_from || []), idB])] }, at);
    if (r.id === idB) return touch({ ...r, distinct_from: [...new Set([...(r.distinct_from || []), idA])] }, at);
    return r;
  });
}

/** Undo any decision between two pages. */
export function clearDecision(records, idA, idB) {
  return records.map(r => {
    if (r.id !== idA && r.id !== idB) return r;
    const other = r.id === idA ? idB : idA;
    return { ...r, supersedes: (r.supersedes || []).filter(x => x !== other), distinct_from: (r.distinct_from || []).filter(x => x !== other) };
  });
}

/**
 * Resolve the register and decide which traced measurements count.
 * @param {object[]} records page register
 * @param {object[]} viewSheets project.sheets (view-level: id, file_id, page_index)
 * @param {object[]} measurements project.measurements (sheet_id = view sheet id)
 * @returns {{resolved:object[], status:Map<string,object>, includedIds:Set<string>, excluded:object[], flagged:object[], error:string|null}}
 */
export function revisionFilter(records, viewSheets, measurements) {
  let resolved = [];
  try { resolved = resolveSheets(toRevisionRecords(records)); }
  catch (err) { return { resolved: [], status: new Map(), includedIds: new Set(), excluded: (measurements || []).map(m => ({ measurement_id: m.id, reason: 'register_error', detail: err.message })), flagged: [], error: err.message }; }
  const status = new Map(resolved.map(r => [r.id, r]));
  const viewToPage = new Map((viewSheets || []).map(s => [s.id, pageRecordId(s.file_id, s.page_index)]));
  const mapped = (measurements || []).map(m => ({ ...m, sheet_id: viewToPage.get(m.sheet_id) || m.sheet_id }));
  const res = filterMeasurementsForTakeoff(mapped, resolved);
  const describe = (e, kind) => {
    const page = status.get(mapped.find(m => m.id === e.measurement_id)?.sheet_id);
    const byIds = page?.superseded_by_ids || (page?.superseded_by ? [page.superseded_by] : []);
    const by = byIds.map(id => records.find(r => r.id === id)).filter(Boolean);
    return { ...e, kind, page_id: page?.id || null, explanation: explain(e.reason, page, by, records) };
  };
  return {
    resolved, status,
    includedIds: new Set((res.included || []).map(m => m.id)),
    excluded: (res.excluded || []).map(e => describe(e, 'excluded')),
    flagged: (res.flagged || []).map(e => describe({ ...e, engine_reason: e.reason, reason: 'clouded_revision' }, 'flagged')),
    error: null,
  };
}

function explain(reason, page, by, records) {
  const self = records.find(r => r.id === page?.id);
  const name = self ? `${self.sheet_number || 'This sheet'} (${pageLabel(self)})` : 'This sheet';
  const byText = by.map(b => `${b.sheet_number || 'sheet'} rev ${b.revision ?? 0} (${pageLabel(b)})`).join(', ');
  switch (reason) {
    case 'superseded_sheet': return `Not counted: ${name} was replaced by ${byText || 'a newer sheet'}. The measurement is kept for reference.`;
    case 'sheet_conflict': return `Not counted yet: two drawings are both numbered ${self?.sheet_number || 'the same'}. Choose which one is current.`;
    case 'clouded_revision': return `Counted, but check it: ${byText || 'a revision'} changed clouded areas of ${name}.`;
    case 'unknown_sheet': return 'Not counted: the drawing page for this measurement is missing.';
    case 'rejected': return 'Not counted: marked rejected.';
    default: return `Not counted (${reason}).`;
  }
}

/** Plain-language status for a page record. */
export function pageStatus(resolvedRecord, records) {
  if (!resolvedRecord) return { code: 'unknown', label: 'Not checked' };
  const by = (resolvedRecord.superseded_by_ids || (resolvedRecord.superseded_by ? [resolvedRecord.superseded_by] : []))
    .map(id => records.find(r => r.id === id)).filter(Boolean);
  switch (resolvedRecord.status) {
    case 'effective': return { code: 'effective', label: 'Current' };
    case 'superseded': return { code: 'superseded', label: 'Replaced by ' + by.map(b => (b.sheet_number || '') + ' rev ' + (b.revision ?? 0)).join(', ') };
    case 'partially_superseded': return { code: 'partial', label: 'Clouded changes in ' + by.map(b => (b.sheet_number || '') + ' rev ' + (b.revision ?? 0)).join(', ') };
    case 'conflict': return { code: 'conflict', label: 'Two versions — choose which is current' };
    default: return { code: resolvedRecord.status, label: resolvedRecord.status };
  }
}
