// MeasuredField Remodel Takeoff — codebasis.mjs (spec §3.4)
//
// Selects the model-code editions that govern ONE project. It never decides
// compliance (always 'NOT_EVALUATED') and never consults today's date: the
// governing date is the project's own permit application date. This module
// deliberately does not reference the global Date object at all — calendar
// arithmetic is done on 'YYYY-MM-DD' strings with integer day numbers.
//
// Precedence per code family:
//   1. Editions stated on approved plans (with an acceptance date) govern the
//      approved scope → FROM_APPROVED_PLANS.
//   2. Otherwise a registry lookup by AHJ + application date →
//      REGISTRY_VERIFIED only when the in-force edition is official-verified,
//      its transition rule is known, and the date is not near a change.
//   3. Anything less certain → NEEDS_AHJ_CONFIRMATION; no AHJ / no date → UNKNOWN.
//
// Registry shape (code-registry.v2):
//   {jurisdictions:[{id, label, editions:[{family, edition, status,
//     effective_on, superseded_on, transition_rule, verification, sources[]}]}]}

export const MODULE_VERSION = '0.2.0';

export const CODE_BASIS_STATUSES = Object.freeze([
  'FROM_APPROVED_PLANS',
  'REGISTRY_VERIFIED',
  'NEEDS_AHJ_CONFIRMATION',
  'UNKNOWN',
]);

/** Families always reported, even when neither plans nor registry mention them. */
export const DEFAULT_FAMILIES = Object.freeze(['IRC', 'IBC', 'IECC', 'NEC']);

/** An application date this close (days, either side) to an edition change needs AHJ confirmation. */
export const TRANSITION_WINDOW_DAYS = 180;

/** The only verification level that can yield REGISTRY_VERIFIED. */
export const VERIFIED_LEVEL = 'official_verified';

/** Compliance is never evaluated by this tool. */
export const COMPLIANCE = 'NOT_EVALUATED';

// Edition statuses that mean "not (yet) adopted": never in force, but their
// dates still count as an upcoming change.
const NOT_IN_FORCE_STATUSES = new Set(['proposed', 'pending', 'draft', 'not_adopted', 'withdrawn']);
// Edition statuses that mean the registry itself has conflicting evidence.
const CONFLICT_STATUSES = new Set(['conflict', 'disputed']);
// Wording that marks a transition rule as not actually known.
const UNKNOWN_TRANSITION = /(unknown|unresolved|not[\s_-]?verified|unverified|unresearched|\btbd\b|to be determined|confirm)/i;

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Error thrown for malformed inputs; `code` is a stable identifier. */
export class CodeBasisError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'CodeBasisError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new CodeBasisError(code, message);
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isBlank(value) {
  return value === undefined || value === null || (typeof value === 'string' && value.trim() === '');
}

// ---------------------------------------------------------------------------
// Calendar arithmetic without Date
// ---------------------------------------------------------------------------

/**
 * Day number (days since 1970-01-01, proleptic Gregorian) for a real
 * 'YYYY-MM-DD' date, or null when the text is not a valid calendar date.
 * Pure integer arithmetic (H. Hinnant's days_from_civil); no Date involved.
 */
export function isoDayNumber(text) {
  if (typeof text !== 'string') return null;
  const match = ISO_DATE.exec(text);
  if (!match) return null;
  const y = Number(match[1]);
  const m = Number(match[2]);
  const d = Number(match[3]);
  if (m < 1 || m > 12 || d < 1) return null;
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
  if (d > daysInMonth) return null;

  const yy = m <= 2 ? y - 1 : y;
  const era = Math.floor(yy / 400);
  const yoe = yy - era * 400;
  const mp = (m + 9) % 12; // March = 0
  const doy = Math.floor((153 * mp + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}

// ---------------------------------------------------------------------------
// Registry helpers
// ---------------------------------------------------------------------------

function verificationLevel(verification) {
  if (typeof verification === 'string') return verification;
  if (isPlainObject(verification)) return verification.level ?? verification.status ?? null;
  return null;
}

function isOfficialVerified(verification) {
  return verificationLevel(verification) === VERIFIED_LEVEL;
}

/** True when a registry transition rule is actually stated (not "unknown", "TBD", "confirm"...). */
export function isTransitionRuleKnown(rule) {
  if (isBlank(rule)) return false;
  if (typeof rule === 'string') return !UNKNOWN_TRANSITION.test(rule);
  if (isPlainObject(rule)) {
    if (rule.known === false) return false;
    const text = rule.text ?? rule.rule ?? rule.summary ?? null;
    if (rule.known === true) return true;
    return typeof text === 'string' && isTransitionRuleKnown(text);
  }
  return false;
}

function familyKey(family) {
  return typeof family === 'string' ? family.trim().toUpperCase() : '';
}

function copySources(sources) {
  if (!Array.isArray(sources)) return [];
  return sources.map((s) => (isPlainObject(s) ? { ...s } : s));
}

// Normalize one registry edition entry; invalid dates are kept as flags.
function normalizeEdition(raw) {
  const effectiveDay = isoDayNumber(raw.effective_on);
  const supersededDay = isoDayNumber(raw.superseded_on);
  return {
    family: familyKey(raw.family),
    edition: isBlank(raw.edition) ? null : String(raw.edition).trim(),
    status: typeof raw.status === 'string' ? raw.status.trim().toLowerCase() : null,
    effective_on: raw.effective_on ?? null,
    superseded_on: raw.superseded_on ?? null,
    effectiveDay,
    supersededDay,
    badEffective: !isBlank(raw.effective_on) && effectiveDay === null,
    badSuperseded: !isBlank(raw.superseded_on) && supersededDay === null,
    transition_rule: raw.transition_rule ?? null,
    verification: raw.verification ?? null,
    sources: copySources(raw.sources),
  };
}

function findJurisdiction(registry, ahjId) {
  if (!registry || !Array.isArray(registry.jurisdictions)) return null;
  return registry.jurisdictions.find((j) => isPlainObject(j) && j.id === ahjId) ?? null;
}

/**
 * Structural checks for a code-registry.v2 document. Returns
 * `{errors:[string], warnings:[string]}`; never throws on bad data.
 */
export function validateRegistry(registry) {
  const errors = [];
  const warnings = [];
  if (!isPlainObject(registry) || !Array.isArray(registry.jurisdictions)) {
    errors.push('registry must be an object with a jurisdictions array');
    return { errors, warnings };
  }
  const ids = new Set();
  registry.jurisdictions.forEach((j, ji) => {
    const where = `jurisdictions[${ji}]`;
    if (!isPlainObject(j) || isBlank(j.id)) {
      errors.push(`${where}: id is required`);
      return;
    }
    if (ids.has(j.id)) errors.push(`${where}: duplicate jurisdiction id ${j.id}`);
    ids.add(j.id);
    if (!Array.isArray(j.editions)) {
      warnings.push(`${where} (${j.id}): no editions array; every lookup will need AHJ confirmation`);
      return;
    }
    const verifiedByFamily = new Map();
    j.editions.forEach((raw, ei) => {
      const at = `${where}.editions[${ei}]`;
      if (!isPlainObject(raw)) {
        errors.push(`${at}: must be an object`);
        return;
      }
      const e = normalizeEdition(raw);
      if (!e.family) errors.push(`${at}: family is required`);
      if (!e.edition) errors.push(`${at}: edition is required`);
      if (e.badEffective) errors.push(`${at}: effective_on is not a valid YYYY-MM-DD date`);
      if (e.badSuperseded) errors.push(`${at}: superseded_on is not a valid YYYY-MM-DD date`);
      if (e.effectiveDay !== null && e.supersededDay !== null && e.supersededDay <= e.effectiveDay) {
        errors.push(`${at}: superseded_on must be after effective_on`);
      }
      if (!isTransitionRuleKnown(e.transition_rule)) warnings.push(`${at}: transition rule unknown`);
      if (isOfficialVerified(e.verification) && e.effectiveDay !== null) {
        if (!verifiedByFamily.has(e.family)) verifiedByFamily.set(e.family, []);
        verifiedByFamily.get(e.family).push({ e, at });
      }
    });
    // Overlapping verified editions of one family make the date lookup ambiguous.
    for (const list of verifiedByFamily.values()) {
      for (let a = 0; a < list.length; a += 1) {
        for (let b = a + 1; b < list.length; b += 1) {
          const x = list[a].e;
          const y = list[b].e;
          const xEnd = x.supersededDay ?? Infinity;
          const yEnd = y.supersededDay ?? Infinity;
          if (x.effectiveDay < yEnd && y.effectiveDay < xEnd) {
            warnings.push(`${list[a].at} and ${list[b].at}: verified ${x.family} editions overlap in time`);
          }
        }
      }
    }
  });
  return { errors, warnings };
}

// Registry lookup for one family on one application day.
function lookupFamily(family, editions, appDay, appDate, jurisdictionLabel) {
  const entries = editions.filter((e) => e.family === family);
  const result = (status, edition, sources, note, candidates = []) => ({
    edition,
    status,
    sources,
    note,
    candidate_editions: candidates,
  });

  if (entries.length === 0) {
    return result(
      'NEEDS_AHJ_CONFIRMATION',
      null,
      [],
      `No ${family} entry in the registry for ${jurisdictionLabel}; ask the AHJ which edition applies.`,
    );
  }

  const reasons = [];
  if (entries.some((e) => e.badEffective || e.badSuperseded)) {
    reasons.push('a registry entry has an unreadable date');
  }
  const inForceCapable = entries.filter((e) => !NOT_IN_FORCE_STATUSES.has(e.status));
  const undated = inForceCapable.filter((e) => e.effectiveDay === null);
  const inForce = inForceCapable.filter(
    (e) => e.effectiveDay !== null && e.effectiveDay <= appDay && (e.supersededDay === null || appDay < e.supersededDay),
  );

  // Editions whose in-force window comes within the transition window of the date.
  const nearby = entries.filter((e) => {
    if (e.effectiveDay === null) return false;
    const end = e.supersededDay ?? Infinity;
    return e.effectiveDay <= appDay + TRANSITION_WINDOW_DAYS && end > appDay - TRANSITION_WINDOW_DAYS;
  });
  const changeDays = [];
  for (const e of entries) {
    for (const [day, text] of [[e.effectiveDay, e.effective_on], [e.supersededDay, e.superseded_on]]) {
      if (day !== null && Math.abs(day - appDay) <= TRANSITION_WINDOW_DAYS) changeDays.push({ day, text });
    }
  }
  if (changeDays.length > 0) {
    const dates = [...new Set(changeDays.map((c) => c.text))].sort();
    reasons.push(`application date ${appDate} is within ${TRANSITION_WINDOW_DAYS} days of an edition change (${dates.join(', ')})`);
  }
  const candidateEditions = (list) => [...new Set(list.map((e) => e.edition).filter(Boolean))];

  if (inForce.length === 0) {
    if (undated.length > 0) reasons.push('the registry does not record an effective date');
    else reasons.push(`no ${family} edition in the registry is in force on ${appDate}`);
    const single = undated.length === 1 ? undated[0] : null;
    return result(
      'NEEDS_AHJ_CONFIRMATION',
      single ? single.edition : null,
      single ? copySources(single.sources) : [],
      `Confirm with the AHJ: ${reasons.join('; ')}.`,
      candidateEditions([...undated, ...nearby]),
    );
  }

  if (inForce.length > 1) {
    reasons.unshift(`several ${family} editions overlap on ${appDate} (${candidateEditions(inForce).join(', ')})`);
    return result(
      'NEEDS_AHJ_CONFIRMATION',
      null,
      inForce.flatMap((e) => copySources(e.sources)),
      `Confirm with the AHJ: ${reasons.join('; ')}.`,
      candidateEditions([...inForce, ...nearby]),
    );
  }

  const chosen = inForce[0];
  if (undated.length > 0) reasons.push('other entries for this family have no effective date');
  if (!isOfficialVerified(chosen.verification)) {
    reasons.push(`edition ${chosen.edition} is not official-verified (verification: ${verificationLevel(chosen.verification) ?? 'none'})`);
  }
  if (CONFLICT_STATUSES.has(chosen.status)) reasons.push('the registry records conflicting evidence for this edition');
  if (!isTransitionRuleKnown(chosen.transition_rule)) reasons.push('the permit transition rule is unknown');

  if (reasons.length === 0) {
    return result(
      'REGISTRY_VERIFIED',
      chosen.edition,
      copySources(chosen.sources),
      `${family} ${chosen.edition} in force on ${appDate} per official-verified registry entry (effective ${chosen.effective_on}). Transition rule: ${typeof chosen.transition_rule === 'string' ? chosen.transition_rule : 'recorded'}. Local amendments are not evaluated.`,
      [chosen.edition],
    );
  }
  return result(
    'NEEDS_AHJ_CONFIRMATION',
    chosen.edition,
    copySources(chosen.sources),
    `Registry indicates ${family} ${chosen.edition} on ${appDate}, but confirm with the AHJ: ${reasons.join('; ')}.`,
    candidateEditions([chosen, ...nearby]),
  );
}

// Normalize approved_plan_basis.editions (object map or array) to a Map.
function planEditions(basis) {
  const map = new Map();
  const add = (family, value) => {
    const key = familyKey(family);
    if (!key) fail('BAD_PLAN_BASIS', 'approved_plan_basis edition entries need a family');
    const edition = isPlainObject(value) ? value.edition : value;
    if (isBlank(edition) || (typeof edition !== 'string' && typeof edition !== 'number')) {
      fail('BAD_PLAN_BASIS', `approved_plan_basis edition for ${key} must be a non-empty string`);
    }
    map.set(key, {
      edition: String(edition).trim(),
      scope: isPlainObject(value) && !isBlank(value.scope) ? String(value.scope) : null,
      ref: isPlainObject(value) && !isBlank(value.ref) ? String(value.ref) : null,
    });
  };
  const editions = basis.editions;
  if (Array.isArray(editions)) {
    for (const item of editions) {
      if (!isPlainObject(item)) fail('BAD_PLAN_BASIS', 'approved_plan_basis.editions[] items must be objects');
      add(item.family, item);
    }
  } else if (isPlainObject(editions)) {
    for (const [family, value] of Object.entries(editions)) add(family, value);
  } else if (editions !== undefined && editions !== null) {
    fail('BAD_PLAN_BASIS', 'approved_plan_basis.editions must be an object or an array');
  }
  return map;
}

/**
 * Select the code basis for one project. Returns
 * `{ahj_id, application_date, source:'approved_plans'|'registry'|'none',
 *   families:{IRC:{edition|null, status, sources, note, candidate_editions}, ...},
 *   warnings:[{code, message, family?}], compliance:'NOT_EVALUATED'}`.
 * Never reads the current date: without an application date the registry
 * cannot be consulted and those families are UNKNOWN. Editions stated on
 * approved plans with an `accepted_on` date are FROM_APPROVED_PLANS (they
 * govern the approved scope) even when the AHJ or date is unknown.
 * Throws CodeBasisError on a malformed application date, plan basis or registry.
 */
export function selectCodeBasis({ registry = null, ahj_id = null, application_date = null, approved_plan_basis = null, families = DEFAULT_FAMILIES } = {}) {
  const warnings = [];
  const warn = (code, message, family) => warnings.push(family ? { code, family, message } : { code, message });

  // --- inputs ---------------------------------------------------------------
  if (registry !== null && registry !== undefined && (!isPlainObject(registry) || !Array.isArray(registry.jurisdictions))) {
    fail('BAD_REGISTRY', 'registry must be an object with a jurisdictions array');
  }
  const ahjId = isBlank(ahj_id) ? null : String(ahj_id);
  const appDate = isBlank(application_date) ? null : application_date;
  let appDay = null;
  if (appDate !== null) {
    appDay = isoDayNumber(appDate);
    if (appDay === null) fail('BAD_DATE', `application_date must be a real calendar date 'YYYY-MM-DD', got ${JSON.stringify(application_date)}`);
  }
  if (!Array.isArray(families)) fail('BAD_FAMILIES', 'families must be an array of code family names');

  let plan = new Map();
  let planAccepted = null;
  if (approved_plan_basis !== null && approved_plan_basis !== undefined) {
    if (!isPlainObject(approved_plan_basis)) fail('BAD_PLAN_BASIS', 'approved_plan_basis must be an object or null');
    plan = planEditions(approved_plan_basis);
    if (!isBlank(approved_plan_basis.accepted_on)) {
      if (isoDayNumber(approved_plan_basis.accepted_on) === null) {
        fail('BAD_DATE', 'approved_plan_basis.accepted_on must be a real calendar date YYYY-MM-DD');
      }
      planAccepted = approved_plan_basis.accepted_on;
    }
  }

  // --- jurisdiction -----------------------------------------------------------
  const jurisdiction = ahjId ? findJurisdiction(registry, ahjId) : null;
  const jurisdictionLabel = jurisdiction?.label ?? ahjId ?? 'this jurisdiction';
  let registryUsable = false;
  if (ahjId === null) {
    warn('NO_AHJ', 'No AHJ selected; registry editions cannot be looked up.');
  } else if (!registry) {
    warn('NO_REGISTRY', 'No code registry loaded; registry editions cannot be looked up.');
  } else if (!jurisdiction) {
    warn('UNKNOWN_AHJ', `AHJ ${ahjId} is not in the registry; every registry family is UNKNOWN.`);
  }
  if (appDate === null) {
    warn('NO_APPLICATION_DATE', 'No permit application date recorded; the registry is not consulted (today\'s date is never used as a substitute).');
  }
  if (jurisdiction && appDate !== null) registryUsable = true;

  const editions = [];
  if (jurisdiction) {
    if (!Array.isArray(jurisdiction.editions)) {
      warn('NO_REGISTRY_EDITIONS', `Registry entry for ${jurisdictionLabel} lists no editions.`);
    } else {
      for (const raw of jurisdiction.editions) if (isPlainObject(raw)) editions.push(normalizeEdition(raw));
    }
  }

  // --- family list (defaults, then registry, then plans; no duplicates) ------
  const familyList = [];
  const addFamily = (f) => {
    const key = familyKey(f);
    if (key && !familyList.includes(key)) familyList.push(key);
  };
  families.forEach(addFamily);
  editions.forEach((e) => addFamily(e.family));
  plan.forEach((_, f) => addFamily(f));

  // --- resolve each family ----------------------------------------------------
  const out = {};
  let anyFromPlans = false;
  if (plan.size > 0 && planAccepted === null) {
    warn('PLAN_BASIS_NOT_ACCEPTED', 'Editions are listed for the plans but no acceptance date is recorded; they are treated as unconfirmed.');
  }

  for (const family of familyList) {
    let registryResult;
    if (registryUsable) {
      registryResult = lookupFamily(family, editions, appDay, appDate, jurisdictionLabel);
    } else {
      const missing = [ahjId === null ? 'no AHJ' : null, ahjId !== null && !jurisdiction ? 'AHJ not in registry' : null, appDate === null ? 'no application date' : null]
        .filter(Boolean)
        .join(', ');
      registryResult = {
        edition: null,
        status: 'UNKNOWN',
        sources: [],
        note: `Registry not consulted (${missing || 'no registry'}).`,
        candidate_editions: [],
      };
    }

    const planned = plan.get(family);
    if (!planned) {
      out[family] = registryResult;
      continue;
    }

    const planSource = { type: 'approved_plans', accepted_on: planAccepted, ref: planned.ref ?? approved_plan_basis.source_ref ?? null };
    const scopeText = planned.scope ? ` for ${planned.scope} scope` : '';
    if (planAccepted !== null) {
      anyFromPlans = true;
      out[family] = {
        edition: planned.edition,
        status: 'FROM_APPROVED_PLANS',
        sources: [planSource],
        note: `${family} ${planned.edition}${scopeText} as stated on the approved plans (accepted ${planAccepted}). Governs the approved scope only; new or changed scope may fall under a different edition, confirm with the AHJ.`,
        candidate_editions: [planned.edition],
        scope: planned.scope,
      };
      if (registryResult.edition && registryResult.edition !== planned.edition) {
        warn(
          'APPROVED_PLANS_DIFFER_FROM_REGISTRY',
          `Approved plans cite ${family} ${planned.edition}; the registry indicates ${registryResult.edition} for ${appDate}. The approved plans govern the approved scope; confirm with the AHJ for any added scope.`,
          family,
        );
      }
    } else {
      out[family] = {
        edition: planned.edition,
        status: 'NEEDS_AHJ_CONFIRMATION',
        sources: [planSource, ...registryResult.sources],
        note: `${family} ${planned.edition}${scopeText} is stated on plans that are not recorded as accepted; confirm with the AHJ.${registryResult.edition ? ` Registry indicates ${registryResult.edition}.` : ''}`,
        candidate_editions: [...new Set([planned.edition, ...registryResult.candidate_editions])],
        scope: planned.scope,
      };
    }
  }

  return {
    ahj_id: ahjId,
    jurisdiction_label: jurisdiction?.label ?? null,
    application_date: appDate,
    source: anyFromPlans ? 'approved_plans' : registryUsable ? 'registry' : 'none',
    families: out,
    warnings,
    compliance: COMPLIANCE,
  };
}
