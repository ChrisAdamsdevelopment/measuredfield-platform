/**
 * plan-facts.mjs — deterministic extraction of printed facts from PDF text.
 *
 * Input is the text layer of the user's own PDF (PDF.js getTextContent items),
 * normalised to display coordinates. Output is a list of *printed facts*, each
 * carrying the page and the exact printed evidence it came from.
 *
 * What this module deliberately does NOT do:
 *  - it never turns a printed number into a measured quantity;
 *  - it never decides code compliance or which code legally governs;
 *  - it never extracts people, street addresses, phone numbers, e-mail
 *    addresses, parcel numbers or signatures (privacy filter below), so the
 *    facts are safe to show, export or send to an optional AI reviewer.
 */
export const MODULE_VERSION = '0.3.0';

const CODE_FAMILIES = ['IRC', 'IBC', 'IEBC', 'IECC', 'IPC', 'IMC', 'IFC', 'IFGC', 'IWUIC', 'NEC', 'UPC', 'UMC', 'IPMC'];
const FAMILY_RE = CODE_FAMILIES.join('|');

/** Privacy filter: drop any captured string that looks like contact or parcel data. */
const PRIVATE_PATTERNS = [
  /[\w.+-]+@[\w-]+\.[\w.]+/,                       // e-mail
  /\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b/,          // phone
  /\b\d{3}-\d{2}-\d{3}[A-Z]?\b/,                     // Arizona-style APN 123-45-678
  /\b\d{2,6}\s+[A-Z][A-Z]+\s+(DR|DRIVE|ST|STREET|AVE|AVENUE|RD|ROAD|LN|LANE|CIR|CIRCLE|CT|COURT|WAY|BLVD|PL|PLACE|TRL|TRAIL|HWY)\b\.?/i, // street address
  /\bLOT\s*\d+/i,
  /\bBY[_:\s]+[a-z]+\.[a-z]+/i,                       // reviewer user ids like By_first.last
];
export function isPrivateText(text) {
  return PRIVATE_PATTERNS.some(re => re.test(String(text || '')));
}

const squash = s => String(s || '').replace(/\s+/g, ' ').trim();
const upper = s => squash(s).toUpperCase().replace(/[’‘]/g, "'").replace(/[”“″]/g, '"');

/**
 * Normalise one page of PDF.js text items.
 * @param {{doc_id?:string, page_index:number, items:{text:string,x:number,y:number}[]}} page
 *   x,y are 0..1 display coordinates with y measured from the TOP of the page as viewed.
 */
function lines(page) {
  return (page.items || [])
    .filter(it => typeof it.text === 'string' && it.text.trim())
    .map(it => ({ text: squash(it.text), up: upper(it.text), x: Number(it.x) || 0, y: Number(it.y) || 0, w: Number(it.w) || 0 }));
}

function evidence(page, text) {
  return { doc_id: page.doc_id ?? null, page_index: page.page_index, text: squash(text).slice(0, 160) };
}

/**
 * Group text lines into layout blocks (a note paragraph, a stacked room label).
 * Lines join a block when they are left-aligned (or centred) with the previous
 * line and sit just below it. Works in display coordinates (y down).
 */
export function textBlocks(ls, { maxGap = 0.022, alignTol = 0.012 } = {}) {
  const sorted = [...ls].sort((a, b) => a.y - b.y || a.x - b.x);
  const used = new Set();
  const blocks = [];
  for (let i = 0; i < sorted.length; i++) {
    if (used.has(i)) continue;
    const block = [sorted[i]]; used.add(i);
    let last = sorted[i];
    for (let j = i + 1; j < sorted.length; j++) {
      if (used.has(j)) continue;
      const c = sorted[j];
      const dy = c.y - last.y;
      if (dy > maxGap) break;
      if (dy <= 0.0005) continue;
      const lastCentre = last.x + (last.w || 0) / 2, cCentre = c.x + (c.w || 0) / 2;
      if (Math.abs(c.x - last.x) <= alignTol || (last.w && c.w && Math.abs(cCentre - lastCentre) <= alignTol)) {
        block.push(c); used.add(j); last = c;
      }
    }
    blocks.push({ text: block.map(b => b.text).join(' '), up: block.map(b => b.up).join(' '), x: block[0].x, y: block[0].y, lines: block.length });
  }
  return blocks;
}

/** Parse feet-inches text such as 3'-9", 10'-0", 2'6", 12' into decimal feet. */
export function parseFeetInches(text) {
  const t = upper(text);
  const m = t.match(/^(\d{1,4})\s*'\s*(?:-?\s*(\d{1,2})(?:\s+(\d{1,2})\/(\d{1,2}))?\s*"?)?/);
  if (!m) return null;
  const ft = Number(m[1]);
  const inch = m[2] ? Number(m[2]) : 0;
  const frac = m[3] && m[4] && Number(m[4]) > 0 ? Number(m[3]) / Number(m[4]) : 0;
  if (inch >= 12) return null;
  return ft + (inch + frac) / 12;
}

/** Parse a printed architectural/engineering scale. Returns null for NTS or unknown formats. */
export function parseScale(text) {
  const t = upper(text).replace(/^SCALE[:\s]*/, '');
  if (/NOT TO SCALE|^N\.?T\.?S\.?$/.test(t)) return { nts: true };
  // e.g. 1/4" = 1'-0"   3/16"=1'-0"   1 1/2" = 1'-0"
  let m = t.match(/^(\d+)?\s*(?:(\d+)\/(\d+))?\s*"\s*=\s*(\d+)\s*'(?:\s*-?\s*(\d+)\s*")?/);
  if (m && (m[1] || m[2])) {
    const whole = m[1] && !m[2] ? Number(m[1]) : (m[1] && m[2] ? Number(m[1]) : 0);
    const frac = m[2] ? Number(m[2]) / Number(m[3]) : 0;
    const paper_in = m[2] ? (m[1] ? whole + frac : frac) : whole;
    const real_ft = Number(m[4]) + (m[5] ? Number(m[5]) / 12 : 0);
    if (paper_in > 0 && real_ft > 0) return { nts: false, paper_in, real_ft, ft_per_pt: real_ft / (paper_in * 72) };
  }
  // e.g. 1" = 10'
  m = t.match(/^(\d+(?:\.\d+)?)\s*(?:"|IN(?:CH)?)\s*=\s*(\d+(?:\.\d+)?)\s*(?:'|FT|FEET)/);
  if (m) {
    const paper_in = Number(m[1]), real_ft = Number(m[2]);
    if (paper_in > 0 && real_ft > 0) return { nts: false, paper_in, real_ft, ft_per_pt: real_ft / (paper_in * 72) };
  }
  return null;
}

function toIsoDate(mdY) {
  const m = String(mdY).match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (!m) return null;
  const mo = Number(m[1]), d = Number(m[2]), y = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}

/* ------------------------------------------------------------------------ */

function extractCodeEditions(page, ls, out) {
  const joined = ls.map(l => l.up).join(' \n ');
  const res = [
    new RegExp(`\\b(20\\d\\d)\\s+(${FAMILY_RE})\\b`, 'g'),
    new RegExp(`\\b(${FAMILY_RE})\\s*(?:20\\d\\d\\s*)?\\(?\\s*(20\\d\\d)\\b`, 'g'),
  ];
  for (const re of res) {
    for (const m of joined.matchAll(re)) {
      const [a, b] = [m[1], m[2]];
      const family = CODE_FAMILIES.includes(a) ? a : b;
      const edition = CODE_FAMILIES.includes(a) ? b : a;
      const start = Math.max(0, m.index - 40);
      const context = joined.slice(start, m.index + m[0].length + 40).replace(/\s*\n\s*/g, ' ');
      const role = /DESIGN BASIS|BUILDING CODE\s*=/.test(joined.slice(Math.max(0, m.index - 80), m.index + 10))
        ? 'design_basis' : (/TABLE|SECTION|PER\s|\(/.test(context) ? 'reference' : 'plan_basis');
      out.push({ family, edition, role, evidence: evidence(page, context) });
    }
  }
}

const AGENCY_RE = /^((?:CITY|TOWN|COUNTY)\s+OF\s+[A-Z][A-Z]+(?:\s+[A-Z][A-Z]+)?|[A-Z][A-Z]+\s+COUNTY)$/;
const STAMP_ACTION_RE = /^(ACCEPTED|APPROVED|REVIEWED|REVISION\s*\d+|REVISED\s+PLANS?)\b/;
const STAMP_DATE_RE = /DATE[\s_:]*(\d{1,2}\/\d{1,2}\/\d{4})/;
/** Review stamps: an agency line with an action word and a date line close by. Person ids are never kept. */
function extractStamps(page, ls, out) {
  const titleCase = t => t.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()).replace(/\bOf\b/g, 'of');
  for (const a of ls) {
    const am = a.up.match(AGENCY_RE);
    if (!am) continue;
    const near = ls.filter(o => o !== a && Math.abs(o.y - a.y) <= 0.03 && Math.abs(o.x - a.x) <= 0.08);
    const byDist = (p, q) => Math.abs(p.y - a.y) - Math.abs(q.y - a.y);
    const act = near.filter(o => STAMP_ACTION_RE.test(o.up)).sort(byDist)[0];
    const dateLine = near.filter(o => STAMP_DATE_RE.test(o.up)).sort(byDist)[0];
    if (!act || !dateLine) continue;
    const date = toIsoDate(dateLine.up.match(STAMP_DATE_RE)[1]);
    if (!date) continue;
    const action = act.up.match(STAMP_ACTION_RE)[1].replace(/\s+/g, ' ');
    const agency = titleCase(am[1]);
    const docId = page.doc_id ?? null;
    if (out.some(x => x.action === action && x.agency === agency && x.date === date && x.evidence.page_index === page.page_index && x.evidence.doc_id === docId)) continue;
    out.push({ action, agency, date, evidence: evidence(page, `${action} - ${agency} - ${date}`) });
  }
}

const AREA_LABEL = /^(REMODEL(?:ED)?\s+AREA|AREA\s+OF\s+(?:WORK|REMODEL)|ADDITION|NEW\s+CONDITIONED|CONDITIONED|LIVABLE|LIVING|GARAGE|COVERED\s+(?:ENTRY|DECK|PATIO|PORCH)|UNDER\s+ROOF|DECK|PATIO|BASEMENT)\b[\s:=-]*(\d{1,6}(?:\.\d{1,2})?)\s*(?:S\.?F\.?|SQ\.?\s*FT\.?)?$/;
const AREA_LABEL_ONLY = /^(REMODEL(?:ED)?\s+AREA|AREA\s+OF\s+(?:WORK|REMODEL)|ADDITION|NEW\s+CONDITIONED|CONDITIONED|LIVABLE|LIVING|GARAGE|COVERED\s+(?:ENTRY|DECK|PATIO|PORCH)|UNDER\s+ROOF|DECK|PATIO|BASEMENT)\s*[:=-]?$/;
const AREA_VALUE = /^\d{1,6}(\.\d{1,2})?\s*(S\.?F\.?|SQ\.?\s*FT\.?)?$/;
function extractAreas(page, ls, out) {
  for (const l of ls) {
    let m = l.up.match(AREA_LABEL);
    if (!m && AREA_LABEL_ONLY.test(l.up)) {
      // Table layout: label and value are separate text items on the same row.
      const val = ls.filter(o => o !== l && Math.abs(o.y - l.y) <= 0.006 && o.x > l.x && o.x - l.x <= 0.2 && AREA_VALUE.test(o.up))
        .sort((p, q) => p.x - q.x)[0];
      if (val) m = [null, l.up.replace(/\s*[:=-]$/, ''), val.up.match(/^\d+(\.\d+)?/)[0]];
    }
    if (!m) continue;
    const docId = page.doc_id ?? null;
    if (out.some(x => x.label === squash(m[1]) && x.evidence.page_index === page.page_index && x.evidence.doc_id === docId)) continue;
    const value = Number(m[2]);
    out.push({ label: squash(m[1]), value, unit: 'sf', is_zero: value === 0, evidence: evidence(page, squash(m[1]) + ' ' + m[2]) });
  }
}

function extractScales(page, ls, out) {
  for (const l of ls) {
    if (!/^SCALE\b|^NOT TO SCALE$|^N\.?T\.?S\.?$/.test(l.up)) continue;
    const parsed = parseScale(l.up);
    out.push({ text: l.text.slice(0, 60), x: l.x, y: l.y, parsed, evidence: evidence(page, l.text) });
  }
}

const OPENING_WORDS = /\b(AWN|AWNING|PKT|POCKET|SH|DH|SLIDER|XO|WIN|WINDOW|WDO|DR|DOOR|BIFOLD|BIPASS|BYPASS|FIXED|CSMT|CASEMENT)\b/;
function extractDimensions(page, ls, out) {
  for (const l of ls) {
    const m = l.up.match(/^(?:EX\.?\s*|NEW\s*|DEMO\s*)?(\d{1,4}\s*'\s*-?\s*\d{0,2}(?:\s+\d\/\d)?\s*"?)\s*(.*)$/);
    if (!m) continue;
    const feet = parseFeetInches(m[1]);
    if (feet === null || feet <= 0) continue;
    const rest = m[2] || '';
    let kind = 'dimension';
    if (/CEILING|CLG/.test(rest)) kind = 'ceiling_height';
    else if (OPENING_WORDS.test(rest) || /^\d{1,2}'\d{1,2}"?[A-Z]{2,}/.test(l.up.replace(/\s/g, ''))) kind = 'opening_callout';
    else if (/TOP OF|FINISHED|F\.?F\.?|PLATE|\bEL\b|ELEV/.test(rest) || (feet >= 90 && !/[±]/.test(l.text))) kind = 'datum_elevation';
    // Words after the dimension ("SETBACK", "±") make a candidate ambiguous for calibration.
    const suffix = rest.replace(/["'\s]+/g, ' ').trim();
    const approximate = /±|\+\/-|\bAPPROX/.test(l.up);
    out.push({ text: l.text.slice(0, 40), feet: Math.round(feet * 10000) / 10000, kind, suffix, approximate, x: l.x, y: l.y, w: l.w || 0, evidence: evidence(page, l.text) });
  }
}

const ACTION_START = /^(?:\d+\.\s*)?(PROVIDE|DEMO(?:LISH)?|REMOVE|NEW|FILL|INSTALL|REPLACE|RELOCATE|VERIFY|DROP|TIE|INFILL|PATCH|REMODELED|ENLARGE|CONVERT|EXISTING\s+WINDOWS?\s+TO\s+BE|PAINT|STUCCO|FLOOR\s+SLOPED)\b/;
const ACTION_ANYWHERE = /\b(ROLL-?\s?IN\s+(CURBLESS\s+)?SHOWER|CURBLESS\s+SHOWER|LINEAR\s+DRAIN|TILE\s+SURROUND|WINDOW\s+INFILL|FILL\s+(IN\s+)?(EXT\.?\s+)?(WINDOW|WDO))\b/;
function extractActionNotes(page, ls, out, blocks) {
  const docId = page.doc_id ?? null;
  const seen = new Set(out.filter(o => o.evidence.page_index === page.page_index && o.evidence.doc_id === docId).map(o => o.text.toUpperCase()));
  const add = text => {
    const key = text.toUpperCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ text, evidence: evidence(page, text) });
  };
  for (const l of ls) {
    if (l.text.length < 6 || l.text.length > 160) continue;
    if (ACTION_START.test(l.up) || ACTION_ANYWHERE.test(l.up)) add(l.text);
  }
  for (const bl of blocks) {
    if (bl.lines < 2 || bl.text.length > 400) continue;
    if (ACTION_START.test(bl.up) || ACTION_ANYWHERE.test(bl.up)) add(bl.text.slice(0, 300));
  }
}

const ROOM_LABEL = /^(EXISTING|EX\.?|REMODELED|NEW|PROPOSED)\s+(MASTER\s+BEDROOM|BEDROOM|RESIDENCE|M\.?\s*BATH|MASTER\s+BATH|BATH(?:ROOM)?|M\.?\s*CLOSET|CLOSET|LINEN\s+CLOSET|DRESSING|KITCHEN|LIVING(?:\s+ROOM)?|GARAGE|LAUNDRY|DINING|HALL(?:WAY)?|DECK|PATIO|OFFICE|WATER\s+CLOSET|ROLL-?IN\s+SHOWER|SHOWER)\b/;
function extractRooms(page, ls, out, blocks) {
  // Room labels are often stacked text items ("EXISTING" / "MASTER BEDROOM"): use layout blocks.
  for (const b of [...blocks.filter(x => x.lines >= 2), ...ls]) {
    const label = b.up;
    if (label.length > 80) continue;
    const m = label.match(ROOM_LABEL);
    if (!m) continue;
    const prefix = m[1].replace('.', '');
    const status = /^(EXISTING|EX)$/.test(prefix) ? 'existing_context' : 'in_scope';
    const name = squash(m[2]);
    if (out.some(r => r.name === name && r.status === status && r.page_index === page.page_index)) continue;
    out.push({ name, status, label: squash(label).slice(0, 60), page_index: page.page_index, evidence: evidence(page, label) });
  }
}

/** The printed "scope of work" paragraph: the block directly under its heading. */
function extractScopeStatements(page, ls, out, blocks) {
  for (const h of ls) {
    if (!/^-*\s*SCOPE\s+OF\s+WORK\s*-*:?$/.test(h.up)) continue;
    const body = blocks.filter(b => b.y > h.y && b.y - h.y <= 0.03 && Math.abs(b.x - h.x) <= 0.03 && b.text.length > 20)
      .sort((p, q) => p.y - q.y)[0];
    if (body) out.push({ text: body.text.slice(0, 600), evidence: evidence(page, body.text.slice(0, 160)) });
  }
}

/* Sheet identity: sheet number, revision number and issue date SUGGESTIONS for one page. */
const SHEET_TOKEN = /^(?:[ACEGLMPST]|[A-Z]{2})-?\d{1,3}(?:\.\d{1,2})?[A-Z]?$/;
function extractSheetIdentity(page, ls, out) {
  const sheetLabels = ls.filter(l => /^SHEET(\s*(NO\.?|NUMBER|#))?:?$/.test(l.up));
  const nearLabel = l => sheetLabels.some(s => Math.abs(s.x - l.x) <= 0.06 && Math.abs(s.y - l.y) <= 0.05);
  const cands = [];
  for (const l of ls) {
    const t = l.up.replace(/\s+/g, '');
    if (t.length > 8) continue;
    const near = nearLabel(l);
    let score = 0;
    if (SHEET_TOKEN.test(t) && /\d\.\d/.test(t)) score = 3;            // S0.0, A1.1
    else if (SHEET_TOKEN.test(t) && t.includes('-')) score = 2;       // A-101
    else if (/^\d{1,2}(\.\d{1,2})?$/.test(t) && near) score = 2;      // "2" under SHEET
    else continue;
    if (near) score += 3;
    if (l.x > 0.75 && l.y > 0.7) score += 1;                          // usual title-block corner
    const prev = cands.find(c => c.text === t);
    if (prev) { prev.score = Math.max(prev.score, score); prev.mentions += 1; continue; }
    cands.push({ text: t, score, mentions: 1, evidence: evidence(page, l.text) });
  }
  cands.sort((a, b) => b.score - a.score || b.mentions - a.mentions);
  const revs = [];
  for (const l of ls) {
    if (/REVISIONS/.test(l.up)) continue;
    const m = l.up.match(/\bREV(?:ISION)?\.?\s*#?\s*(\d{1,2})\b/);
    if (m) revs.push({ number: Number(m[1]), evidence: evidence(page, 'REVISION ' + m[1]) });
  }
  const dateLabels = ls.filter(l => /^DATE:?$/.test(l.up));
  const dates = [];
  for (const l of ls) {
    let m = l.up.match(/^(?:DATE[:\s]+)?(\d{1,2}\/\d{1,2}\/\d{4})$/);
    if (!m) continue;
    const iso = toIsoDate(m[1]);
    if (!iso) continue;
    const near = /^DATE/.test(l.up) || dateLabels.some(d => Math.abs(d.x - l.x) <= 0.08 && Math.abs(d.y - l.y) <= 0.04);
    if (!dates.some(d => d.iso === iso)) dates.push({ iso, label: m[1], near_label: near, evidence: evidence(page, l.text) });
  }
  out.push({
    doc_id: page.doc_id ?? null, page_index: page.page_index,
    sheet_number: cands[0]?.text || null,
    sheet_number_candidates: cands.slice(0, 4),
    revision: revs.length ? Math.max(...revs.map(r => r.number)) : null,
    revision_evidence: revs.slice(0, 3),
    // Title blocks often list the original and the revision date; suggest the latest one.
    issued_on: dates.length ? dates.map(d => d.iso).sort().at(-1) : null,
    date_candidates: dates.slice(0, 5),
    status: 'SUGGESTED_FROM_PRINTED_TEXT',
  });
}

const META = [
  ['occupancy', /OCCUPANCY\s*[:=]?\s*([A-Z]-?\d)\b/],
  ['construction_type', /CONSTRUCTION\s+TYPE\s*[:=]?\s*([IV]{1,3}-?[AB])\b/],
  ['project_type', /PROJECT\s+TYPE\s*[:=]?\s*(SFR|MFR|COMMERCIAL|RESIDENTIAL)\b/],
  ['climate_zone', /CLIMATE\s+ZONE\s*(\d[A-C]?)\b/],
  ['floor_live_load_psf', /LIVE\s+LOAD\s*=?\s*(\d{2,3})\s*PSF/],
  ['floor_dead_load_psf', /DEAD\s+LOAD\s*=?\s*(\d{2,3})\s*PSF/],
  ['risk_category', /RISK\s+CATEGORY\s*=?\s*(I{1,3}V?|IV)\b/],
  ['presumptive_bearing_psf', /PRESUMPTIVE\s+BEARING\s+CAPACITY\s+OF\s+(\d{3,5})\s*PSF/],
];
function extractMeta(page, ls, out) {
  const joined = ls.map(l => l.up).join(' ');
  for (const [key, re] of META) {
    const m = joined.match(re);
    if (m && !out.some(x => x.key === key)) out.push({ key, value: m[1], evidence: evidence(page, m[0]) });
  }
}

const CALLOUTS = [
  /\bLVL\s*\d-\d{1,2}\b/g,
  /\(\d\)\s*2X\d{1,2}\b/g,
  /\b(?:LUS|LSSR|LRU|HUC|HU|IUS|BC|ABU|A|LPCZ|BCS)\d{1,3}[A-Z]?(?:-\d)?Z?\b/g,
  /\bTITEN\s+HD\b/g,
  /\b\d{1,2}"\s*DIA\.?\s*SONOTUBE\b/g,
  /\b3\/4"\s*T&G\b/g,
  /\bTIMBERSTRAND\b/g,
];
function extractStructuralCallouts(page, ls, out) {
  const joined = ls.map(l => l.up).join(' ');
  for (const re of CALLOUTS) {
    for (const m of joined.matchAll(re)) {
      const text = squash(m[0]);
      // Single-letter "A" prefix is too generic unless followed by Simpson-style digits (A34, A35).
      if (/^A\d/.test(text) && !/^A3[45]/.test(text)) continue;
      const found = out.find(c => c.text === text);
      if (found) { found.mentions += 1; continue; }
      out.push({ text, mentions: 1, page_index: page.page_index, evidence: evidence(page, text) });
    }
  }
}

/**
 * Extract printed facts from every page.
 * @param {Array<{doc_id?:string,page_index:number,items:{text:string,x:number,y:number}[]}>} pages
 */
export function extractPlanFacts(pages) {
  if (!Array.isArray(pages)) throw new TypeError('pages must be an array');
  const facts = {
    schema: 'mf.takeoff.plan-facts/1', module_version: MODULE_VERSION,
    code_editions: [], agency_stamps: [], areas: [], scales: [], dimensions: [],
    action_notes: [], rooms: [], scope_statements: [], sheet_identities: [], meta: [], structural_callouts: [],
    privacy_dropped: 0,
  };
  for (const page of pages) {
    if (!Number.isInteger(page?.page_index)) throw new TypeError('each page needs an integer page_index');
    const all = lines(page);
    const safe = all.filter(l => !isPrivateText(l.text));
    facts.privacy_dropped += all.length - safe.length;
    extractCodeEditions(page, safe, facts.code_editions);
    extractStamps(page, all, facts.agency_stamps); // stamps keep agency+date only (person ids never captured)
    extractAreas(page, safe, facts.areas);
    extractScales(page, safe, facts.scales);
    extractDimensions(page, safe, facts.dimensions);
    const blocks = textBlocks(safe);
    extractActionNotes(page, safe, facts.action_notes, blocks);
    extractRooms(page, safe, facts.rooms, blocks);
    extractScopeStatements(page, safe, facts.scope_statements, blocks);
    extractMeta(page, safe, facts.meta);
    extractStructuralCallouts(page, safe, facts.structural_callouts);
    extractSheetIdentity(page, safe, facts.sheet_identities);
  }
  facts.code_summary = summarizeCodes(facts.code_editions);
  return facts;
}

/** Most-cited edition per family, with conflicts flagged rather than resolved. */
export function summarizeCodes(editions) {
  const byFamily = new Map();
  for (const e of editions) {
    if (!byFamily.has(e.family)) byFamily.set(e.family, new Map());
    const m = byFamily.get(e.family);
    const k = e.edition;
    const rec = m.get(k) || { edition: k, mentions: 0, roles: new Set(), evidence: [] };
    rec.mentions += 1; rec.roles.add(e.role);
    if (rec.evidence.length < 3) rec.evidence.push(e.evidence);
    m.set(k, rec);
  }
  const summary = [];
  for (const [family, m] of byFamily) {
    const editionsSorted = [...m.values()].sort((a, b) => b.mentions - a.mentions);
    const top = editionsSorted[0];
    summary.push({
      family,
      edition: top.edition,
      roles: [...top.roles],
      mentions: top.mentions,
      conflict: editionsSorted.length > 1,
      other_editions: editionsSorted.slice(1).map(e => e.edition),
      evidence: top.evidence,
      status: 'PRINTED_ON_PLANS',
    });
  }
  const order = ['IRC', 'IBC', 'IEBC', 'IECC', 'NEC', 'IPC', 'UPC', 'IMC', 'UMC', 'IFC', 'IFGC', 'IWUIC', 'IPMC'];
  return summary.sort((a, b) => order.indexOf(a.family) - order.indexOf(b.family));
}
