/**
 * scope-items.mjs — turns printed plan facts (and optional AI suggestions) into a
 * reviewable job-scope checklist.
 *
 * A scope item answers "what work is shown?", not "how much?". It names the work
 * type (an assembly id from assemblies.mjs), the action (demolish / new / modify),
 * the measurement the contractor must trace, and the printed evidence. Every item
 * starts as `proposed`; only the contractor can `accept` it. Accepting never creates
 * a measurement, quantity or price.
 *
 * Rules below are general construction vocabulary, not tied to any one project.
 */
export const MODULE_VERSION = '0.3.0';

export const WORK_TYPES = Object.freeze([
  'wall_drywall', 'wall_framing', 'tile_wall', 'tile_floor', 'cement_board',
  'waterproofing_membrane', 'shower_pan_mortar_bed', 'linear_drain', 'insulation_batt',
  'window_infill', 'demolition', 'paint', 'base_trim', 'count_item', 'structural_member', 'allowance',
]);
export const ACTIONS = Object.freeze(['demolish', 'new', 'modify', 'existing_remain', 'verify']);
export const SCOPE_STATUSES = Object.freeze(['proposed', 'accepted', 'rejected']);
export const SOURCES = Object.freeze(['plan_text', 'ai', 'manual']);

const T = (key, title, work_type, action, measure, extra = {}) => ({ key, title, work_type, action, measure, ...extra });

/** Phrase rules: [regex over a printed note, items it implies]. */
export const SCOPE_RULES = Object.freeze([
  { id: 'curbless_shower', re: /ROLL-?\s?IN\s+(CURBLESS\s+)?SHOWER|CURBLESS\s+SHOWER/, items: [
    T('shower_floor_tile', 'Shower floor tile', 'tile_floor', 'new', 'polygon', { hint: 'Trace the shower floor on the proposed floor plan.', needs: ['owner_selection'] }),
    T('shower_mortar_bed', 'Sloped mortar bed to drain', 'shower_pan_mortar_bed', 'new', 'polygon', { hint: 'Same footprint as the shower floor; confirm the slope and depth.' }),
    T('shower_waterproofing', 'Shower waterproofing membrane', 'waterproofing_membrane', 'new', 'polyline', { hint: 'Trace the shower walls; floor area is added from the floor trace.', needs: ['owner_selection'] }),
    T('shower_wall_tile', 'Shower wall tile', 'tile_wall', 'new', 'polyline', { hint: 'Trace each tiled wall run; enter the tile height.', needs: ['owner_selection'] }),
    T('shower_backer', 'Cement backer board behind tile', 'cement_board', 'new', 'polyline', { hint: 'Same wall runs as the tile.' }),
  ] },
  { id: 'linear_drain', re: /LINEAR\s+DRAIN/, items: [
    T('linear_drain', 'Linear shower drain', 'linear_drain', 'new', 'count', { hint: 'Count the drain; length and model are a product selection.', needs: ['owner_selection'], price_class: 'supplier_quote' }),
  ] },
  { id: 'grab_bar_blocking', re: /GRAB\s+BARS?\s+(REINFORCEMENT|BLOCKING)|BLOCKING\s+FOR\s+GRAB/, items: [
    T('grab_bar_blocking', 'Grab bar reinforcement / blocking', 'count_item', 'new', 'count', { hint: 'Count each keynote location.' }),
  ] },
  { id: 'demo_shower', re: /DEMO(LISH)?\s+(EXISTING\s+)?SHOWER|REMOVE\s+(EXISTING\s+)?SHOWER/, items: [
    T('demo_shower', 'Demolish existing shower', 'demolition', 'demolish', 'polygon', { hint: 'Trace the existing shower on the existing/demo plan.' }),
  ] },
  { id: 'demo_walls', re: /DEMO(LISH)?(ED)?\s+(EXISTING\s+)?WALL|DEMOLISHED\s+WALL/, items: [
    T('demo_walls', 'Demolish walls', 'demolition', 'demolish', 'polyline', { hint: 'Trace the dashed (demolished) walls on the existing/demo plan.' }),
  ] },
  { id: 'demo_doors', re: /DEMO(LISH)?\s+\d+'?-?\d*"?\s*[^\s]*\s*(BIFOLD|DOOR|PKT|POCKET)|DEMO(LISH)?\s+(EXISTING\s+)?DOOR/, items: [
    T('demo_doors', 'Remove doors', 'demolition', 'demolish', 'count', { hint: 'Count doors marked DEMO.' }),
  ] },
  { id: 'window_infill', re: /FILL\s+(IN\s+)?(EXT\.?|EXTERIOR\s+)?(WINDOW|WDO)|WINDOW\s+INFILL|WINDOWS?\s+TO\s+BE\s+DEMOLISHED/, items: [
    T('window_infill', 'Exterior window infill', 'window_infill', 'new', 'count', { hint: 'Count windows to fill; enter width and height from the plan callout.', needs: ['field_verify'] }),
  ] },
  { id: 'new_partition', re: /NEW\s+(LINEN\s+)?CLOSET|NEW\s+WALL|NEW\s+PARTITION/, items: [
    T('new_wall_framing', 'New wall framing', 'wall_framing', 'new', 'polyline', { hint: 'Trace new walls on the proposed plan (check the legend for 2x4 vs 2x6).' }),
    T('new_wall_drywall', 'New wall drywall', 'wall_drywall', 'new', 'polyline', { hint: 'Same runs as the new framing; two faces for interior partitions.' }),
  ] },
  { id: 'structural_floor', re: /NEW\s+JOISTS?|NEW\s+BEAM|NEW\s+HANGERS?|REMOVE\s+EXISTING\s+I-?JOIST|PER\s+ENGINEERING/, items: [
    T('structural_floor_framing', 'Structural floor framing per engineer', 'structural_member', 'new', 'count', { hint: 'Enter each member exactly as called out on the effective structural sheet.', needs: ['engineer_spec'], price_class: 'supplier_quote' }),
  ] },
  { id: 'subfloor_removal', re: /REMOVE\s+EXISTING\s+I-?JOIST\s*&\s*SUBFLOOR|REMOVE\s+(EXISTING\s+)?SUBFLOOR/, items: [
    T('demo_subfloor', 'Remove floor framing and subfloor at shower', 'demolition', 'demolish', 'polygon', { hint: 'Trace the removed floor area.' }),
  ] },
  { id: 'insulation', re: /R-?\s?\d{2}\s+INSULATION|INSULATION\s+REPLACE|VERIFY\s+MIN\.?\s+R-?\d{2}/, items: [
    T('insulation', 'Insulation (verify / replace)', 'insulation_batt', 'verify', 'polyline', { hint: 'Trace the wall or floor runs where insulation is replaced.', needs: ['field_verify'] }),
  ] },
  { id: 'tile_surround', re: /TILE\s+SURROUND|CEMENT\s+BOARD/, items: [
    T('shower_wall_tile', 'Shower wall tile', 'tile_wall', 'new', 'polyline', { hint: 'Trace each tiled wall run; enter the tile height.', needs: ['owner_selection'] }),
    T('shower_backer', 'Cement backer board behind tile', 'cement_board', 'new', 'polyline', { hint: 'Same wall runs as the tile.' }),
  ] },
  { id: 'paint', re: /\bPAINT\b/, items: [
    T('paint', 'Paint', 'paint', 'new', 'polyline', { hint: 'Trace painted wall runs; the color is the owner\'s selection.', needs: ['owner_selection'] }),
  ] },
]);

function slug(s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, ''); }

/**
 * Propose scope items from extracted plan facts.
 * @returns {{items:object[], in_scope_areas:object[], context_areas:object[], work_area:object|null}}
 */
export function proposeScopeFromFacts(facts) {
  if (!facts || !Array.isArray(facts.action_notes)) throw new TypeError('plan facts required');
  const byKey = new Map();
  const notes = [...facts.action_notes];
  for (const r of facts.rooms || []) {
    if (r.status === 'in_scope') notes.push({ text: r.label, evidence: r.evidence });
  }
  for (const note of notes) {
    const up = String(note.text).toUpperCase();
    for (const rule of SCOPE_RULES) {
      if (!rule.re.test(up)) continue;
      for (const tpl of rule.items) {
        const existing = byKey.get(tpl.key);
        if (existing) {
          if (existing.evidence.length < 4 && !existing.evidence.some(e => e.text === note.evidence.text)) existing.evidence.push(note.evidence);
          continue;
        }
        byKey.set(tpl.key, makeItem({ ...tpl, rule: rule.id, source: 'plan_text', evidence: [note.evidence] }));
      }
    }
  }
  const work = (facts.areas || []).find(a => /REMODEL|AREA OF WORK|ADDITION/i.test(a.label) && !a.is_zero) || null;
  return {
    items: [...byKey.values()],
    in_scope_areas: dedupeAreas((facts.rooms || []).filter(r => r.status === 'in_scope')),
    // A room that is remodeled was "existing" on the demo plan; it is work, not context.
    context_areas: dedupeAreas((facts.rooms || []).filter(r => r.status === 'existing_context'))
      .filter(c => !(facts.rooms || []).some(r => r.status === 'in_scope' && r.name === c.name)),
    work_area: work ? { label: work.label, value_sf: work.value, evidence: work.evidence, status: 'PRINTED_NOT_MEASURED' } : null,
  };
}

function dedupeAreas(rooms) {
  const seen = new Map();
  for (const r of rooms) if (!seen.has(r.name)) seen.set(r.name, { name: r.name, evidence: [r.evidence] });
  return [...seen.values()];
}

function makeItem(fields) {
  if (!WORK_TYPES.includes(fields.work_type)) throw new RangeError('unknown work_type ' + fields.work_type);
  if (!ACTIONS.includes(fields.action)) throw new RangeError('unknown action ' + fields.action);
  return {
    id: 'scope_' + slug(fields.key),
    key: fields.key,
    title: fields.title,
    work_type: fields.work_type,
    action: fields.action,
    measure: fields.measure,
    hint: fields.hint || '',
    needs: fields.needs ? [...fields.needs] : [],
    price_class: fields.price_class || (fields.action === 'demolish' ? 'allowance' : 'retail'),
    source: fields.source,
    sources: [fields.source],
    rule: fields.rule || null,
    evidence: fields.evidence || [],
    location: fields.location || null,
    status: 'proposed',
    decided_at: null,
    note: '',
  };
}

/* ---------------------------- AI suggestions ---------------------------- */

const short = (v, n) => (typeof v === 'string' ? v.replace(/\s+/g, ' ').trim().slice(0, n) : '');
const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '');

/**
 * Validate an AI "read the scope" response. Never trusts numbers: quantity hints are
 * kept only as printed text. Evidence phrases are checked against the page text.
 * @param {object} payload provider response {actual_inference, provider, model, analysis}
 * @param {{page_index:number, doc_id:string, page_text?:string, at:string}} ctx  `at` is required (no implicit clock)
 */
export function validateAiScope(payload, ctx) {
  if (!ctx || !Number.isInteger(ctx.page_index) || typeof ctx.doc_id !== 'string' || !ctx.doc_id) throw new TypeError('page_index and doc_id required');
  if (typeof ctx.at !== 'string' || !ctx.at) throw new TypeError('ctx.at timestamp required');
  if (payload?.actual_inference !== true) throw new Error('A real model response is required');
  const a = payload.analysis;
  if (!a || typeof a !== 'object' || Array.isArray(a)) throw new Error('Model returned no structured scope');
  const pageText = norm(ctx.page_text || '');
  const ev = text => {
    const t = short(text, 160);
    const n = norm(t.replace(/^(heading|label|note|title)[:\s]+/i, ''));
    return { doc_id: ctx.doc_id, page_index: ctx.page_index, text: t, source_text_match: Boolean(pageText && n.length >= 6 && pageText.includes(n)) };
  };
  const list = (v, max) => (Array.isArray(v) ? v.filter(x => x && typeof x === 'object' && !Array.isArray(x)).slice(0, max) : []);
  const items = [];
  for (const w of list(a.work_items, 30)) {
    const work_type = WORK_TYPES.includes(w.work_type) ? w.work_type : 'allowance';
    const action = ACTIONS.includes(w.action) ? w.action : 'verify';
    const title = short(w.item, 90) || 'Unlabelled work item';
    const evidence = ev(w.evidence);
    items.push({
      ...makeItem({
        key: 'ai_' + slug(work_type + '_' + action + '_' + title).slice(0, 60),
        title, work_type, action,
        measure: ['polygon', 'polyline', 'count', 'line'].includes(w.measure) ? w.measure : 'count',
        hint: short(w.location, 100) ? 'Location: ' + short(w.location, 100) : '',
        needs: Array.isArray(w.needs) ? w.needs.filter(n => ['field_verify', 'engineer_spec', 'owner_selection'].includes(n)) : [],
        source: 'ai', evidence: evidence.text ? [evidence] : [], location: short(w.location, 100) || null,
      }),
      printed_quantity_text: short(w.printed_quantity_text, 40) || null,
      ai_unverified: true,
    });
  }
  const areas = (v, status) => list(v, 20).map(x => ({ name: short(x.name, 60) || 'Unnamed area', status, evidence: [ev(x.evidence)] }));
  return {
    schema: 'mf.takeoff.ai-scope/1',
    created_at: ctx.at,
    provider: short(payload.provider, 40) || 'unknown',
    model: short(payload.model, 120) || 'unknown',
    page_index: ctx.page_index, doc_id: ctx.doc_id,
    scope_summary: short(a.scope_summary, 600),
    in_scope_areas: areas(a.in_scope_areas, 'in_scope'),
    context_areas: areas(a.context_only_areas, 'existing_context'),
    items,
    selections_needed: Array.isArray(a.selections_needed) ? a.selections_needed.filter(s => typeof s === 'string').slice(0, 15).map(s => short(s, 120)) : [],
    unknowns: Array.isArray(a.unknowns) ? a.unknowns.filter(s => typeof s === 'string').slice(0, 15).map(s => short(s, 160)) : [],
    needs_human_review: true,
  };
}

/* ------------------------------ Merging -------------------------------- */

/**
 * Merge new proposals into an existing scope list without losing contractor decisions.
 * Items match on (work_type, action) plus key; matching items gain evidence and sources.
 */
export function mergeScope(existing, proposals) {
  const out = (existing || []).map(i => ({ ...i, evidence: [...(i.evidence || [])], sources: [...(i.sources || [i.source])] }));
  for (const p of proposals || []) {
    const match = out.find(i => i.key === p.key) ||
      out.find(i => i.work_type === p.work_type && i.action === p.action && (p.source === 'ai' || i.source === 'ai'));
    if (match) {
      for (const e of p.evidence || []) if (match.evidence.length < 6 && !match.evidence.some(x => x.text === e.text && x.page_index === e.page_index)) match.evidence.push(e);
      if (!match.sources.includes(p.source)) match.sources.push(p.source);
      continue;
    }
    out.push({ ...p, evidence: [...(p.evidence || [])], sources: [...(p.sources || [p.source])] });
  }
  return out;
}

/** Contractor decision. Pure: returns a new item. */
export function decideScopeItem(item, decision, { at, note = '' } = {}) {
  if (!['accepted', 'rejected', 'proposed'].includes(decision)) throw new RangeError('decision must be accepted, rejected or proposed');
  if (typeof at !== 'string' || !at) throw new TypeError('decision timestamp required');
  return { ...item, status: decision, decided_at: decision === 'proposed' ? null : at, note: String(note).slice(0, 300) };
}

export function manualScopeItem({ title, work_type, action = 'new', measure = 'polyline', at }) {
  if (typeof at !== 'string' || !at) throw new TypeError('timestamp required');
  const item = makeItem({ key: 'manual_' + slug(title || work_type) + '_' + slug(at).slice(-6), title: short(title, 90) || work_type, work_type, action, measure, source: 'manual', evidence: [] });
  return { ...item, status: 'accepted', decided_at: at };
}

/**
 * Progress of accepted scope items against recorded measurements.
 * A measurement counts for an item when it carries `scope_item_id` = item.id, or
 * (legacy) its assembly id equals the item's work type.
 */
export function scopeProgress(items, measurements) {
  const accepted = (items || []).filter(i => i.status === 'accepted');
  const rows = accepted.map(i => {
    const ms = (measurements || []).filter(m => m.scope_item_id === i.id || (!m.scope_item_id && m.assembly?.id === i.work_type));
    return { id: i.id, title: i.title, work_type: i.work_type, measured: ms.length > 0, measurement_count: ms.length };
  });
  return {
    accepted: accepted.length,
    measured: rows.filter(r => r.measured).length,
    proposed_open: (items || []).filter(i => i.status === 'proposed').length,
    rows,
  };
}

/* -------------------------- Progress stages ---------------------------- */

/**
 * Stages of a work item, in order. One recorded measurement never makes an item
 * complete: the contractor confirms the quantity is complete, and any open needs
 * (engineer callout, owner selection, field check) must be resolved.
 */
export const STAGES = Object.freeze({
  proposed:        { order: 0, label: 'Waiting for your decision', next: 'Accept it or mark it not in this job' },
  not_in_job:      { order: -1, label: 'Not in this job', next: '' },
  accepted:        { order: 1, label: 'Accepted · not measured yet', next: 'Measure it on the current drawing' },
  needs_remeasure: { order: 1, label: 'Only measured on a replaced or unresolved drawing', next: 'Measure it on the current sheet' },
  measured_draft:  { order: 2, label: 'Measured on an unverified scale', next: 'Verify the drawing scale' },
  measured_review: { order: 2, label: 'Measured — check against a clouded revision', next: 'Confirm it is outside the revised clouds' },
  recorded:        { order: 3, label: 'Measurement recorded', next: 'Confirm when everything for this item is measured' },
  needs_info:      { order: 4, label: 'Quantity complete · more information needed', next: 'Resolve the open items' },
  ready:           { order: 5, label: 'Ready for estimating', next: '' },
});
const NEED_TEXT = { engineer_spec: "engineer's callout", owner_selection: 'owner/product selection', field_verify: 'field check' };

/**
 * @param {object} item scope item
 * @param {{id:string, scope_item_id?:string, assembly?:{id:string}, counted?:'yes'|'no'|'flagged', verified?:boolean}[]} measurements
 */
export function scopeItemStage(item, measurements) {
  if (item.status === 'rejected') return stage('not_in_job', item, []);
  if (item.status !== 'accepted') return stage('proposed', item, []);
  const linked = (measurements || []).filter(m => m.scope_item_id === item.id || (!m.scope_item_id && m.assembly?.id === item.work_type));
  const counted = linked.filter(m => m.counted !== 'no');
  if (!linked.length) return stage('accepted', item, linked);
  if (!counted.length) return stage('needs_remeasure', item, linked);
  if (counted.some(m => m.verified === false)) return stage('measured_draft', item, linked);
  if (counted.some(m => m.counted === 'flagged')) return stage('measured_review', item, linked);
  if (!item.quantity_confirmed_at) return stage('recorded', item, linked);
  const open = openNeeds(item);
  if (open.length) return { ...stage('needs_info', item, linked), open_needs: open, label: 'Quantity complete · still needs ' + open.map(n => NEED_TEXT[n] || n).join(', ') };
  return stage('ready', item, linked);
}
function stage(code, item, linked) {
  return { code, ...STAGES[code], measurement_count: linked.length, counted_count: linked.filter(m => m.counted !== 'no').length, open_needs: openNeeds(item) };
}
export function openNeeds(item) {
  const done = new Set((item.resolved_needs || []).map(r => r.need));
  return (item.needs || []).filter(n => !done.has(n));
}

/** Contractor confirms the item's quantity is complete. Only allowed from the 'recorded' stage. */
export function confirmQuantityComplete(item, measurements, { at }) {
  if (typeof at !== 'string' || !at) throw new TypeError('timestamp required');
  const s = scopeItemStage(item, measurements);
  if (s.code !== 'recorded') throw new Error('Cannot confirm yet: ' + s.label.toLowerCase() + '. ' + s.next + '.');
  return { ...item, quantity_confirmed_at: at };
}
export function reopenQuantity(item) {
  const { quantity_confirmed_at, ...rest } = item;
  return { ...rest, quantity_confirmed_at: null };
}
/** Mark one open need (engineer callout, owner selection, field check) resolved, with a note. */
export function resolveNeed(item, need, { at, note = '' }) {
  if (typeof at !== 'string' || !at) throw new TypeError('timestamp required');
  if (!(item.needs || []).includes(need)) throw new RangeError('item has no need ' + need);
  const rest = (item.resolved_needs || []).filter(r => r.need !== need);
  return { ...item, resolved_needs: [...rest, { need, at, note: String(note).slice(0, 200) }] };
}

/** Stage-aware progress for the checklist (keeps the older accepted/measured fields). */
export function scopeStageSummary(items, measurements) {
  const accepted = (items || []).filter(i => i.status === 'accepted');
  const rows = accepted.map(i => ({ id: i.id, title: i.title, work_type: i.work_type, stage: scopeItemStage(i, measurements) }));
  const n = code => rows.filter(r => r.stage.code === code).length;
  return {
    accepted: accepted.length,
    ready: n('ready'),
    complete: rows.filter(r => r.stage.order >= 4).length,
    measured: rows.filter(r => r.stage.counted_count > 0).length,
    to_measure: n('accepted') + n('needs_remeasure'),
    attention: n('measured_draft') + n('measured_review') + n('needs_info'),
    proposed_open: (items || []).filter(i => i.status === 'proposed').length,
    rows,
  };
}
