/* MF-020: local, same-origin, authenticated saved-workspace bridge. No provider calls. */
(() => {
  "use strict";
  const app = document.getElementById("mf-lab");
  if (!app || !app.dataset.storageApi) return;
  const base = app.dataset.storageApi;
  const writeEnabled = app.dataset.writeEnabled === "true";
  const $ = (name) => document.getElementById(name);
  const state = { snapshots: [], questions: [], experiments: [], active: null, busy: false };

  function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function status(message, error = false) {
    const target = $("mf-save-status");
    if (!target) return;
    target.textContent = message;
    target.classList.toggle("ml-error", error);
  }
  function localState() {
    let value = null;
    app.dispatchEvent(new CustomEvent("mf:request-visibility-state", {
      detail: { resolve(result) { value = result; } },
    }));
    return value;
  }
  function selectedCsvIsSaved(lab) {
    return state.active && lab && state.active.csv === lab.csv_text;
  }
  function updateButtons() {
    const lab = localState();
    const canSave = writeEnabled && !state.busy;
    const eligibleCsv = lab && lab.has_analysis && !lab.synthetic_sample;
    const selections = (lab && Array.isArray(lab.selected)) ? lab.selected : [];
    const hasGscSelection = selections.some((choice) => choice.source !== "benchmark");
    const readyGsc = Boolean(eligibleCsv && selectedCsvIsSaved(lab));
    if ($("mf-save-gsc")) $("mf-save-gsc").disabled = !canSave || !eligibleCsv;
    if ($("mf-save-questions")) $("mf-save-questions").disabled =
      !canSave || !eligibleCsv || !selectedCsvIsSaved(lab) || !lab.approved.length;
    if ($("mf-save-plan")) $("mf-save-plan").disabled =
      !canSave || !lab || !lab.has_plan || !selections.length ||
      (hasGscSelection && !readyGsc);
    if (!writeEnabled) status("Read-only workspace: you can inspect saved research, but cannot change it.");
  }
  async function request(path, body) {
    const init = {
      credentials: "same-origin", mode: "same-origin", cache: "no-store",
    };
    if (body !== undefined) {
      init.method = "POST";
      init.headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": app.dataset.csrf,
      };
      init.body = JSON.stringify(body);
    }
    const response = await fetch(base + path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail :
        "Unable to access saved records (HTTP " + response.status + ").");
    }
    return data;
  }
  function addMetric(container, count, label) {
    const box = node("div");
    box.className = "ml-metric";
    box.append(node("strong", count), node("span", label));
    container.append(box);
  }
  function detailRow(section, title, description, onClick) {
    const row = node("div");
    row.className = "ml-proposal-card";
    if (onClick) {
      const button = node("button", title);
      button.className = "ml-chip-btn";
      button.type = "button";
      button.addEventListener("click", onClick);
      row.append(button);
    } else {
      row.append(node("strong", title));
    }
    row.append(node("p", description));
    section.append(row);
  }
  function displaySaved() {
    const metrics = $("mf-save-stats");
    const list = $("mf-saved-list");
    if (!metrics || !list) return;
    metrics.replaceChildren();
    addMetric(metrics, state.snapshots.length, "Saved GSC sources (up to 100 shown)");
    addMetric(metrics, state.questions.length, "Saved questions (up to 250 shown)");
    addMetric(metrics, state.questions.filter((q) => q.status === "approved").length,
      "Approved questions shown");
    addMetric(metrics, state.experiments.length, "Saved research plans (up to 100 shown)");
    list.replaceChildren();
    for (const [heading, records] of [
      ["Sources", state.snapshots], ["Questions", state.questions],
      ["Research plans", state.experiments],
    ]) {
      const section = node("section");
      section.append(node("h3", heading + " — " + records.length));
      if (!records.length) section.append(node("p", "No saved records in this category."));
      for (const item of records.slice(0, 25)) {
        if (heading === "Sources") {
          const windowLabel = item.date_start && item.date_end
            ? item.date_start + " to " + item.date_end : "Reporting period unknown";
          detailRow(section, item.filename + " · " + item.row_count + " rows",
            windowLabel + " · " + item.impressions + " organic impressions · " +
              item.clicks + " organic clicks · owner-supplied CSV",
            async () => {
              try {
                const saved = await request("/snapshots/" + encodeURIComponent(item.id));
                const detail = node("details");
                detail.open = true;
                detail.append(node("summary", "Saved query rows: " + saved.row_count));
                const table = node("table");
                table.className = "ml-table";
                for (const row of (saved.rows || []).slice(0, 100)) {
                  const tr = node("tr");
                  for (const field of ["query", "impressions", "clicks", "avg_position"]) {
                    tr.append(node("td", row[field] ?? ""));
                  }
                  table.append(tr);
                }
                detail.append(table);
                section.append(detail);
                status("Viewing stored owner-supplied GSC rows; these are not AI search results.");
              } catch (error) { status(error.message, true); }
            });
        } else if (heading === "Questions") {
          detailRow(section, item.text,
            item.status + " · " + item.origin + " · " + item.market, null);
        } else {
          detailRow(section, item.title,
            item.status + " · " + item.request_count + " planned · " +
            item.provider_requests_executed + " executed provider calls", async () => {
              try {
                const saved = await request("/experiments/" + encodeURIComponent(item.id));
                const card = node("pre", JSON.stringify(saved.configuration, null, 2));
                card.className = "ml-prompt";
                section.append(card);
                status("Viewing saved research configuration; no search was executed.");
              } catch (error) { status(error.message, true); }
            });
        }
      }
      if (records.length > 25) section.append(node("p",
        "First 25 items shown in this category; use workspace exports for older records."));
      list.append(section);
    }
    updateButtons();
  }
  async function refresh() {
    state.busy = true;
    updateButtons();
    try {
      const [sources, questions, plans] = await Promise.all([
        request("/snapshots"), request("/questions"), request("/experiments"),
      ]);
      state.snapshots = sources.snapshots || [];
      state.questions = questions.questions || [];
      state.experiments = plans.experiments || [];
      displaySaved();
      if (writeEnabled) status("Saved workspace loaded. No live searches performed.");
    } catch (error) {
      status("Saved workspace unavailable: " + error.message, true);
    } finally {
      state.busy = false;
      updateButtons();
    }
  }
  async function guarded(action) {
    if (!writeEnabled || state.busy) return;
    state.busy = true;
    updateButtons();
    try { await action(); }
    catch (error) { status(error.message, true); }
    finally { state.busy = false; updateButtons(); }
  }
  async function saveGsc() {
    const lab = localState();
    if (!lab || !lab.has_analysis || lab.synthetic_sample) {
      throw new Error("Analyze a genuine owner-supplied GSC CSV before saving. Synthetic samples are excluded.");
    }
    const start = $("mf-source-start")?.value || null;
    const end = $("mf-source-end")?.value || null;
    if (Boolean(start) !== Boolean(end)) throw new Error("Provide both GSC dates or neither.");
    const file = $("ml-file")?.files?.[0];
    const filename = file && /^[a-z0-9_. -]{1,200}$/i.test(file.name)
      ? file.name : "owner-pasted-gsc.csv";
    const result = await request("/snapshots", {
      csv_text: lab.csv_text, filename, date_start: start, date_end: end,
    });
    state.active = { id: result.snapshot.id, csv: lab.csv_text };
    const drafts = await request("/snapshots/" + encodeURIComponent(state.active.id) + "/suggestions", {});
    await refresh();
    status("Saved " + result.snapshot.row_count + " GSC rows (" +
      (start ? start + " through " + end : "reporting period unknown") +
      "). " + drafts.created + " questions saved as drafts; none auto-approved.");
  }
  async function saveApproved() {
    const lab = localState();
    if (!selectedCsvIsSaved(lab) || lab.synthetic_sample) {
      throw new Error("Save the current analyzed GSC source before approving questions.");
    }
    if (!lab.approved.length) throw new Error("Check at least one question to explicitly approve.");
    const snapshotId = state.active.id;
    const all = await request("/questions");
    const claimed = new Set();
    let reviewed = 0;
    for (const approval of lab.approved) {
      const q = (all.questions || []).find((item) =>
        item.origin === "gsc_suggestion" && item.text === approval.text &&
        item.snapshot_id === snapshotId && !claimed.has(item.id));
      if (!q) throw new Error("Question is missing from the active saved GSC source; import or refresh the source before approving.");
      claimed.add(q.id);
      if (q.status !== "approved") {
        await request("/questions/" + encodeURIComponent(q.id) + "/review", { status: "approved" });
        reviewed += 1;
      }
    }
    await refresh();
    status("Approved " + reviewed + " newly reviewed saved GSC question(s). " +
      lab.approved.length + " are selected; unselected drafts remain unapproved.");
  }
  async function savePlan() {
    const lab = localState();
    if (!lab || !lab.has_plan || !lab.selected.length) {
      throw new Error("Build a research plan and select questions before saving.");
    }
    const hasGscSelection = lab.selected.some((choice) => choice.source !== "benchmark");
    if (hasGscSelection && (lab.synthetic_sample || !lab.has_analysis || !selectedCsvIsSaved(lab))) {
      throw new Error("Save the current genuine GSC source and approve its questions before saving this plan.");
    }
    const questions = [];
    for (const choice of lab.selected) {
      if (choice.source === "benchmark") {
        questions.push({ id: choice.id, text: choice.text });
      } else {
        const match = state.questions.find((q) => q.text === choice.text &&
          q.origin === "gsc_suggestion" && q.status === "approved" &&
          q.snapshot_id === state.active.id);
        if (!match) throw new Error("Save and explicitly approve GSC question '" +
          choice.text + "' before saving a plan.");
        questions.push({ id: match.id, text: match.text });
      }
    }
    const title = ($("mf-plan-title")?.value || "").trim();
    if (!title || title.length > 160) throw new Error("Give this saved plan a name (1–160 characters).");
    const result = await request("/experiments", {
      title, questions, markets: lab.config.markets, modes: lab.config.modes,
      repetitions: lab.config.repetitions, chunk_size: lab.config.chunk_size,
      max_total: lab.config.max_total,
    });
    await refresh();
    status("Saved plan '" + result.experiment.title + "' with " +
      result.experiment.request_count + " planned requests. 0 provider calls executed; no billing.");
  }
  $("mf-refresh-saved")?.addEventListener("click", refresh);
  $("mf-save-gsc")?.addEventListener("click", () => guarded(saveGsc));
  $("mf-save-questions")?.addEventListener("click", () => guarded(saveApproved));
  $("mf-save-plan")?.addEventListener("click", () => guarded(savePlan));
  app.addEventListener("mf:visibility-changed", updateButtons);
  updateButtons();
  refresh();
})();
