/*
 * Job scope panel: shows what the uploaded plans SAY (printed facts) and a
 * reviewable scope checklist. All text from PDFs/AI is rendered with textContent.
 * Nothing here measures, prices or approves anything.
 */
import { WORK_TYPES } from "../../src/scope-items.mjs";

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
};
const button = (label, cls, onClick, attrs = {}) => {
  const b = el("button", cls, label);
  b.type = "button";
  for (const [k, v] of Object.entries(attrs)) b.setAttribute(k, v);
  b.addEventListener("click", onClick);
  return b;
};
const prettyDate = iso => {
  const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return iso || "";
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return months[Number(m[2]) - 1] + " " + Number(m[3]) + ", " + m[1];
};
const ACTION_LABEL = { demolish: "Demolish", new: "New work", modify: "Modify", existing_remain: "Existing", verify: "Verify" };
const NEED_LABEL = { engineer_spec: "Engineer's callout", owner_selection: "Owner/product selection", field_verify: "Verify in field" };
const WORK_LABEL = {
  wall_drywall: "Drywall", wall_framing: "Wall framing", tile_wall: "Wall tile", tile_floor: "Floor tile",
  cement_board: "Cement board", waterproofing_membrane: "Waterproofing", shower_pan_mortar_bed: "Mortar bed",
  linear_drain: "Linear drain", insulation_batt: "Insulation", window_infill: "Window infill", demolition: "Demolition",
  paint: "Paint", base_trim: "Base trim", count_item: "Counted item", structural_member: "Structural member", allowance: "Allowance",
};
export const workLabel = id => WORK_LABEL[id] || id;

/** Merge per-document facts for display. */
export function combineFacts(list) {
  const out = { code_summary: [], agency_stamps: [], areas: [], meta: [], scales: [] };
  const codes = new Map();
  for (const f of list.filter(Boolean)) {
    for (const c of f.code_summary || []) {
      const prev = codes.get(c.family);
      if (!prev || c.mentions > prev.mentions || (c.roles || []).includes("design_basis")) {
        const merged = prev && prev.edition !== c.edition ? { ...c, conflict: true, other_editions: [...new Set([...(c.other_editions || []), prev.edition])] } : c;
        codes.set(c.family, merged);
      }
    }
    for (const s of f.agency_stamps || []) if (!out.agency_stamps.some(x => x.action === s.action && x.agency === s.agency && x.date === s.date)) out.agency_stamps.push(s);
    for (const a of f.areas || []) if (!out.areas.some(x => x.label === a.label)) out.areas.push(a);
    for (const m of f.meta || []) if (!out.meta.some(x => x.key === m.key)) out.meta.push(m);
  }
  out.code_summary = [...codes.values()];
  out.agency_stamps.sort((a, b) => a.date.localeCompare(b.date));
  return out;
}

function chip(text, cls = "") { return el("span", "scope-chip " + cls, text); }

function evidenceLine(ev) {
  const p = el("p", "scope-evidence");
  const page = el("span", "scope-evidence-page", "Page " + (Number(ev.page_index) + 1));
  const quote = el("q", "", ev.text);
  p.append(page, document.createTextNode(" "), quote);
  if (ev.source_text_match === false) p.append(el("span", "scope-flag", " · not found in the page text"));
  return p;
}

/**
 * @param {HTMLElement} root
 * @param {object} ctx
 */
export function renderJobScope(root, ctx) {
  const { facts, boundary, items, codeBasis, ai, handlers } = ctx;
  root.replaceChildren();
  const hasFacts = facts && (facts.code_summary.length || facts.agency_stamps.length || facts.areas.length || facts.meta.length);
  root.hidden = !(hasFacts || items.length || ai.available);
  if (root.hidden) return;

  const head = el("div", "section-top");
  const hd = el("div");
  hd.append(el("h2", "", "Work suggested by your plans"),
    el("p", "", "These suggestions come from printed notes in your PDF" + (ai.available ? " and optional AI reading when you request it" : "") +
      ". Each item has a page reference. Check that it belongs in this job. Including an item does not mean it has been measured or priced."));
  head.append(hd);
  root.append(head);

  // ---- What the plans say -------------------------------------------------
  const grid = el("div", "scope-grid");
  const said = el("div", "scope-card");
  said.append(el("h3", "", "What the plans say"));
  if (facts.code_summary.length) {
    const row = el("div", "js-row");
    row.append(el("strong", "", "Code books named in the plans"));
    const chips = el("div", "scope-chips");
    for (const c of facts.code_summary) chips.append(chip(c.edition + " " + c.family + (c.roles?.includes("design_basis") ? " (structural basis)" : ""), c.conflict ? "chip-warn" : ""));
    row.append(chips);
    if (facts.code_summary.some(c => c.conflict)) row.append(el("p", "scope-flag", "More than one edition is printed for a code family. Confirm with the permit office."));
    if (codeBasis.applied) {
      row.append(el("p", "scope-ok", "✓ Project code basis set from these approved plans" + (codeBasis.accepted_on ? " (accepted " + prettyDate(codeBasis.accepted_on) + ")" : "") + ". Code compliance is still not evaluated."));
    } else {
      row.append(el("p", "scope-help", "For a permitted job, the editions on the approved plans govern that approved scope, not today's newest book."));
      row.append(button("Use as this project's code basis", "button button-outline compact", handlers.applyCodeBasis));
    }
    said.append(row);
  }
  if (facts.agency_stamps.length) {
    const row = el("div", "js-row");
    row.append(el("strong", "", "Review stamps"));
    const ul = el("ul", "js-list");
    for (const s of facts.agency_stamps) ul.append(el("li", "", s.action.charAt(0) + s.action.slice(1).toLowerCase() + " · " + s.agency + " · " + prettyDate(s.date)));
    row.append(ul);
    said.append(row);
  }
  const work = facts.areas.find(a => /REMODEL|AREA OF WORK|ADDITION/i.test(a.label) && !a.is_zero);
  if (work) {
    const row = el("div", "js-row");
    row.append(el("strong", "", "Work area stated on the plans"), el("p", "", work.label.toLowerCase().replace(/^\w/, c => c.toUpperCase()) + ": " + work.value + " SF"),
      el("p", "scope-help", "Printed on the title block, not measured. Trace the rooms to get quantities."));
    said.append(row);
  }
  if (facts.meta.length) {
    const labels = { occupancy: "Occupancy", construction_type: "Construction type", project_type: "Project type", climate_zone: "Climate zone",
      floor_live_load_psf: "Floor live load (psf)", floor_dead_load_psf: "Floor dead load (psf)", risk_category: "Risk category", presumptive_bearing_psf: "Soil bearing (psf)" };
    const row = el("div", "js-row");
    row.append(el("strong", "", "Project data printed"));
    const chips = el("div", "scope-chips");
    for (const m of facts.meta) chips.append(chip((labels[m.key] || m.key) + ": " + m.value));
    row.append(chips);
    said.append(row);
  }
  if (!said.querySelector(".js-row")) said.append(el("p", "scope-help", "No code editions, stamps or areas were found in this PDF's text. Scanned (image-only) plans need a manual read or the AI reader."));
  grid.append(said);

  // ---- Scope boundary ------------------------------------------------------
  const bound = el("div", "scope-card");
  bound.append(el("h3", "", "Where the work is"));
  const inScope = boundary.in_scope, context = boundary.context;
  const inRow = el("div", "js-row");
  inRow.append(el("strong", "", "Rooms that appear to have work"));
  const inChips = el("div", "scope-chips");
  for (const a of inScope) inChips.append(chip(a.name, "chip-good"));
  if (!inScope.length) inChips.append(el("span", "scope-help", "None labeled yet"));
  inRow.append(inChips);
  const ctxRow = el("div", "js-row");
  ctxRow.append(el("strong", "", "Rooms shown as existing"));
  const ctxChips = el("div", "scope-chips");
  for (const a of context) ctxChips.append(chip(a.name, "chip-muted"));
  if (!context.length) ctxChips.append(el("span", "scope-help", "None found"));
  ctxRow.append(ctxChips,
    el("p", "scope-help", "Existing rooms may be shown so you can locate the remodel. Include them only if the job calls for work there."));
  bound.append(inRow, ctxRow);
  if (ai.summary) bound.append(el("div", "scope-ai-summary", "AI reading: " + ai.summary));
  grid.append(bound);
  root.append(grid);

  // ---- Checklist ----------------------------------------------------------
  const list = el("div", "scope-card scope-checklist");
  const top = el("div", "scope-checklist-top");
  const proposed = items.filter(i => i.status === "proposed");
  const accepted = items.filter(i => i.status === "accepted");
  const t = el("div");
  const sum = ctx.summary;
  t.append(el("h3", "", "Work to measure and price"),
    el("p", "scope-help", items.length
      ? accepted.length + " accepted · " + proposed.length + " waiting for your decision · " + items.filter(i => i.status === "rejected").length + " not in this job"
      : "No work items were found in the printed notes yet."));
  if (sum && sum.accepted) t.append(el("p", "scope-progress-line", sum.ready + " ready for estimating · " + sum.measured + " measured · " + sum.to_measure + " still to measure" + (sum.attention ? " · " + sum.attention + " need attention" : "")));
  top.append(t);
  const actions = el("div", "scope-actions");
  const planProposed = proposed.filter(i => i.sources?.includes("plan_text"));
  if (planProposed.length) actions.append(button("Accept all " + planProposed.length + " from plan notes", "button button-outline compact", handlers.acceptAllPlanText));
  if (ai.available) {
    const pick = el("select", "scope-ai-page");
    pick.setAttribute("aria-label", "Page for the AI to read");
    for (const o of ai.pageOptions) { const opt = el("option", "", o.label); opt.value = o.value; pick.append(opt); }
    if (ai.defaultPage) pick.value = ai.defaultPage;
    const go = button(ai.busy ? "AI is reading…" : "Read this page's scope with AI", "button button-terra compact", () => handlers.analyzeScope(pick.value));
    go.disabled = ai.busy;
    actions.append(pick, go);
  }
  top.append(actions);
  list.append(top);
  if (ai.available) list.append(el("p", "scope-help", "AI reading sends the chosen page image and its text to the NVIDIA model you configured. Its suggestions are marked AI and need your decision like any other."));

  const ul = el("ul", "scope-items");
  const order = { accepted: 0, proposed: 1, rejected: 2 };
  for (const it of [...items].sort((a, b) => order[a.status] - order[b.status])) {
    const li = el("li", "scope-item scope-" + it.status);
    const main = el("div", "scope-item-main");
    const title = el("div", "scope-item-title");
    title.append(el("strong", "", it.title));
    title.append(chip(ACTION_LABEL[it.action] || it.action, it.action === "demolish" ? "chip-demo" : ""));
    title.append(chip(workLabel(it.work_type), "chip-muted"));
    for (const s of it.sources || [it.source]) title.append(chip(s === "ai" ? "AI suggestion" : s === "manual" ? "Added by you" : "Plan text", s === "ai" ? "chip-ai" : ""));
    for (const n of it.needs || []) title.append(chip(NEED_LABEL[n] || n, "chip-warn"));
    if (it.price_class === "supplier_quote") title.append(chip("Supplier quote", "chip-warn"));
    main.append(title);
    for (const ev of (it.evidence || []).slice(0, 2)) main.append(evidenceLine(ev));
    const st = ctx.stages?.get(it.id);
    if (st && it.status === "accepted") {
      const line = el("p", "scope-stage scope-stage-" + st.code);
      line.append(el("strong", "", st.label));
      if (st.next) line.append(document.createTextNode(" — " + st.next));
      main.append(line);
    }
    if (it.hint && (!st || ["proposed", "accepted"].includes(st.code))) main.append(el("p", "scope-help", it.hint));
    if (it.printed_quantity_text) main.append(el("p", "scope-help", "Printed dimension the AI pointed to: " + it.printed_quantity_text + " (verify by tracing)"));
    li.append(main);
    const btns = el("div", "scope-item-buttons");
    if (it.status !== "accepted") btns.append(button("Accept", "button button-dark compact", () => handlers.decide(it.id, "accepted"), { "aria-label": "Accept " + it.title }));
    if (it.status === "accepted") {
      const code = st?.code;
      if (code === "recorded") btns.append(button("Quantity complete", "button button-dark compact", () => handlers.confirmComplete(it.id), { "aria-label": "Confirm quantity complete for " + it.title }));
      if (code === "needs_info") for (const need of st.open_needs || []) btns.append(button("Mark " + (NEED_LABEL[need] || need).toLowerCase() + " done", "button button-outline compact", () => handlers.resolveNeed(it.id, need), { "aria-label": "Mark " + need + " resolved for " + it.title }));
      if (["needs_info", "ready"].includes(code)) btns.append(button("Reopen", "button button-quiet compact", () => handlers.reopen(it.id), { "aria-label": "Reopen quantity for " + it.title }));
      else btns.append(button(st?.counted_count ? "Measure more" : "Measure →", "button button-terra compact", () => handlers.measure(it.id), { "aria-label": "Measure " + it.title }));
    }
    if (it.status !== "rejected") btns.append(button("Not in this job", "button button-quiet compact", () => handlers.decide(it.id, "rejected"), { "aria-label": "Mark " + it.title + " not in this job" }));
    else btns.append(button("Undo", "button button-quiet compact", () => handlers.decide(it.id, "proposed"), { "aria-label": "Undo decision for " + it.title }));
    li.append(btns);
    ul.append(li);
  }
  list.append(ul);

  // Manual add
  const add = el("form", "scope-add");
  add.addEventListener("submit", e => e.preventDefault());
  const titleIn = el("input");
  titleIn.placeholder = "Add a work item the plans don't spell out (e.g. vanity light)";
  titleIn.maxLength = 90;
  titleIn.setAttribute("aria-label", "New work item name");
  const typeSel = el("select");
  typeSel.setAttribute("aria-label", "Work type");
  for (const w of WORK_TYPES) { const o = el("option", "", workLabel(w)); o.value = w; typeSel.append(o); }
  typeSel.value = "count_item";
  add.append(titleIn, typeSel, button("Add item", "button button-outline compact", () => {
    if (!titleIn.value.trim()) { titleIn.focus(); return; }
    handlers.addManual(titleIn.value.trim(), typeSel.value);
  }));
  list.append(add);

  if (ai.selections?.length || ai.unknowns?.length) {
    const box = el("div", "scope-open-questions");
    if (ai.selections?.length) { box.append(el("strong", "", "Selections the plans leave open (AI)")); const u = el("ul", "js-list"); for (const s of ai.selections) u.append(el("li", "", s)); box.append(u); }
    if (ai.unknowns?.length) { box.append(el("strong", "", "Unclear on the drawing (AI)")); const u = el("ul", "js-list"); for (const s of ai.unknowns) u.append(el("li", "", s)); box.append(u); }
    list.append(box);
  }
  root.append(list);
}

/** Measure screen: accepted scope items still to trace. */
export function renderScopeTodo(root, { progress, items, activeId, onPick, onClear }) {
  root.replaceChildren();
  root.hidden = !progress.accepted;
  if (root.hidden) return;
  const head = el("div", "scope-todo-head");
  head.append(el("strong", "", "Job scope: " + progress.ready + " of " + progress.accepted + " ready · " + progress.measured + " measured · " + progress.to_measure + " to measure"));
  if (activeId) head.append(button("Clear selection", "button button-quiet compact", onClear));
  root.append(head);
  const wrap = el("div", "scope-todo-items");
  for (const r of progress.rows) {
    const it = items.find(i => i.id === r.id);
    const code = r.stage?.code;
    const mark = code === "ready" ? "✓ " : r.stage?.counted_count ? "● " : "";
    const b = button(mark + r.title, "scope-todo-item" + (r.id === activeId ? " is-active" : "") + (code === "ready" ? " is-done" : r.stage?.counted_count ? " is-started" : ""), () => onPick(r.id), { "aria-pressed": String(r.id === activeId) });
    b.title = (r.stage ? r.stage.label + ". " : "") + (it?.hint || "");
    wrap.append(b);
  }
  root.append(wrap);
  const active = items.find(i => i.id === activeId);
  const activeRow = progress.rows.find(r => r.id === activeId);
  if (active) root.append(el("p", "scope-help", (activeRow?.stage ? activeRow.stage.label + (activeRow.stage.next ? " — " + activeRow.stage.next : "") + ". " : "") + (active.hint || "")));
}
