/*
 * "Which drawings are current" panel. Page-level sheet register with contractor
 * decisions on revisions. PDF-derived text is rendered with textContent only.
 */
import { pageLabel, pageStatus } from "../../src/sheet-register.mjs";

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
};
const btn = (label, cls, onClick, attrs = {}) => {
  const b = el("button", cls, label);
  b.type = "button";
  for (const [k, v] of Object.entries(attrs)) b.setAttribute(k, v);
  b.addEventListener("click", onClick);
  return b;
};
const short = r => (r.sheet_number || "No number") + " · rev " + (r.revision ?? 0) + (r.issued_on ? " · " + r.issued_on : "");

/**
 * @param {HTMLElement} root
 * @param {{records:object[], status:Map, groups:object[], excludedCount:number, handlers:object}} ctx
 */
export function renderRevisionPanel(root, ctx) {
  const { records, status, groups, handlers } = ctx;
  root.replaceChildren();
  root.hidden = !records.length;
  if (root.hidden) return;

  const head = el("div", "section-top");
  const hd = el("div");
  hd.append(el("h2", "", "Which drawings are current"),
    el("p", "", "Sheet numbers and revisions below were read from each page — check them. When two pages are versions of the same sheet, you decide which one is current. Measurements on a replaced sheet are kept but not counted."));
  head.append(hd);
  root.append(head);

  // ---- Unresolved duplicates first: this is the action the contractor must take ----
  for (const g of groups) {
    const card = el("div", "rev-alert");
    card.setAttribute("role", "group");
    card.setAttribute("aria-label", "Two versions of sheet " + g.sheet_number);
    card.append(el("strong", "", g.records.length + " pages are all numbered " + g.sheet_number + ". Which one is current?"));
    card.append(el("p", "scope-help", "Until you choose, quantities measured on these pages are not counted. The app suggests the higher revision, but you decide — check the stamps and revision notes."));
    const pick = el("div", "rev-choice");
    const sel = el("select");
    sel.setAttribute("aria-label", "Current version of " + g.sheet_number);
    for (const r of g.records) {
      const o = el("option", "", pageLabel(r) + " — " + short(r) + (r.accepted_on ? " · stamped " + r.accepted_on : ""));
      o.value = r.id;
      sel.append(o);
    }
    sel.value = g.suggested_current;
    const modeWhole = el("label", "rev-radio");
    const rw = el("input"); rw.type = "radio"; rw.name = "mode-" + g.sheet_number; rw.value = "whole_sheet"; rw.checked = true;
    modeWhole.append(rw, document.createTextNode(" It replaces the whole older sheet"));
    const modeCloud = el("label", "rev-radio");
    const rc = el("input"); rc.type = "radio"; rc.name = "mode-" + g.sheet_number; rc.value = "clouded_only";
    modeCloud.append(rc, document.createTextNode(" It changes only clouded areas"));
    const lab = el("label", "rev-label", "Current version");
    lab.append(sel);
    pick.append(lab, modeWhole, modeCloud);
    card.append(pick);
    const actions = el("div", "rev-actions");
    actions.append(
      btn("Confirm current version", "button button-terra compact", () => {
        const mode = rc.checked ? "clouded_only" : "whole_sheet";
        handlers.link(g.records.map(r => r.id), sel.value, mode);
      }, { "data-action": "confirm-current" }),
      btn("These are different sheets", "button button-quiet compact", () => handlers.distinct(g.records.map(r => r.id)), { "data-action": "distinct" }),
    );
    card.append(actions);
    root.append(card);
  }

  // ---- Decisions already made (with undo) ----
  const links = [];
  for (const r of records) {
    for (const oldId of r.supersedes || []) {
      const old = records.find(x => x.id === oldId);
      if (old) links.push({ cur: r, old });
    }
  }
  const distinctPairs = [];
  for (const r of records) for (const o of r.distinct_from || []) if (r.id < o) distinctPairs.push([r, records.find(x => x.id === o)]);
  if (links.length || distinctPairs.length) {
    const ul = el("ul", "rev-decisions");
    for (const { cur, old } of links) {
      const li = el("li");
      li.append(el("span", "", "✓ " + short(cur) + " (" + pageLabel(cur) + ") " + (cur.supersession === "clouded_only" ? "changes clouded areas of " : "replaces ") + short(old) + " (" + pageLabel(old) + ")"));
      li.append(btn("Undo", "button button-quiet compact", () => handlers.undo(cur.id, old.id), { "aria-label": "Undo revision decision for " + cur.sheet_number }));
      ul.append(li);
    }
    for (const [a, b] of distinctPairs) {
      if (!b) continue;
      const li = el("li");
      li.append(el("span", "", "✓ " + pageLabel(a) + " and " + pageLabel(b) + " are different sheets"));
      li.append(btn("Undo", "button button-quiet compact", () => handlers.undo(a.id, b.id)));
      ul.append(li);
    }
    root.append(ul);
  }

  // ---- Register table ----
  const table = el("div", "rev-table");
  table.setAttribute("role", "table");
  const hdr = el("div", "rev-row rev-head");
  hdr.setAttribute("role", "row");
  for (const t of ["Page", "Sheet number", "Revision", "Issue date", "Status", ""]) { const c = el("span", "", t); c.setAttribute("role", "columnheader"); hdr.append(c); }
  table.append(hdr);
  for (const r of records) {
    const st = pageStatus(status.get(r.id), records);
    const row = el("div", "rev-row rev-" + st.code);
    row.setAttribute("role", "row");
    row.dataset.page = r.id;
    const page = el("span", "rev-page", pageLabel(r));
    const num = el("input"); num.value = r.sheet_number || ""; num.maxLength = 24; num.setAttribute("aria-label", "Sheet number for " + pageLabel(r)); num.placeholder = "e.g. A2.1";
    const rev = el("input"); rev.type = "number"; rev.min = "0"; rev.max = "99"; rev.step = "1"; rev.value = String(r.revision ?? 0); rev.setAttribute("aria-label", "Revision for " + pageLabel(r));
    const date = el("input"); date.type = "date"; date.value = r.issued_on || ""; date.setAttribute("aria-label", "Issue date for " + pageLabel(r));
    const stat = el("span", "rev-status rev-status-" + st.code, st.label);
    const save = btn(r.confirmed ? "Save" : "Confirm", "button " + (r.confirmed ? "button-quiet" : "button-outline") + " compact", () => {
      handlers.confirm(r.id, { sheet_number: num.value, revision: rev.value === "" ? 0 : Number(rev.value), issued_on: date.value || null });
    }, { "aria-label": (r.confirmed ? "Save" : "Confirm") + " sheet details for " + pageLabel(r) });
    const conf = el("span", "rev-confirmed", r.confirmed ? "✓ checked" : "suggested");
    const cell = el("span", "rev-cell-actions"); cell.append(save, conf);
    for (const [label, node] of [["Page", page], ["Sheet number", num], ["Revision", rev], ["Issue date", date], ["Status", stat], ["", cell]]) {
      const c = el("span", "rev-cell"); c.setAttribute("role", "cell"); c.dataset.label = label; c.append(node); row.append(c);
    }
    table.append(row);
  }
  root.append(table);
  if (ctx.excludedCount) root.append(el("p", "scope-flag", ctx.excludedCount + " measured quantit" + (ctx.excludedCount === 1 ? "y is" : "ies are") + " not counted because of these decisions. See each item in Step 2."));
}
