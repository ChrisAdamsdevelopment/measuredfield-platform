/* MeasuredField AI Visibility Lab: private local preview, no external provider calls, no automatic localStorage. */
(() => {
  "use strict";
  const app = document.getElementById("mf-lab");
  if (!app) return;
  const $ = (id) => document.getElementById(id);
  const allBenchmarks = Array.from(document.querySelectorAll(".ml-benchmark"));
  const allMarkets = () => Array.from(document.querySelectorAll('input[name="ml-market"]'));
  const allModes = () => Array.from(document.querySelectorAll('input[name="ml-mode"]'));
  const suggestionBox = $("ml-suggestions");

  const SAMPLE_GSC_CSV = [
    "Query,Clicks,Impressions,Position",
    "prescott general contractors,1,42,6.4",
    "prescott valley kitchen contractor,0,19,14.2",
    "bathroom remodeling prescott valley az,0,15,11.8",
    "custom finish carpentry prescott az,0,11,8.5",
    "chino valley home remodeling contractor,0,9,16.0",
    "dewey humboldt bathroom renovation,0,6,19.3",
    "prescotts best construction,3,28,1.2",
    "construction superintendent jobs prescott,0,4,38.0",
  ].join("\n");

  const METHOD_LABELS = {
    as_written: "As written",
    controlled_substitution: "Controlled city substitution",
    persona_context: "Homeowner location context",
  };

  const STATUS_LABELS = {
    eligible: { text: "Eligible homeowner query", cls: "ml-badge" },
    brand: { text: "Branded query (excluded from new drafts)", cls: "ml-badge ml-badge-warn" },
    excluded_service_or_employment: { text: "Excluded service / employment", cls: "ml-badge ml-badge-quarantine" },
  };

  let suggestions = [];
  let parsedSnapshot = null;
  let latestPlan = null;

  const selectedBenchmark = () => allBenchmarks.filter((box) => box.checked).map((box) => ({
    id: box.value, text: box.dataset.text, source: "benchmark",
  }));
  const approvedQuestions = () => suggestions.filter((item) => item.approved === true).map((item) => ({
    id: item.id, text: item.question, source: "gsc_approved",
  }));
  const chosen = () => [...selectedBenchmark(), ...approvedQuestions()];
  const checkedMarkets = () => allMarkets().filter((box) => box.checked).map((box) => box.value);
  const checkedModes = () => allModes().filter((box) => box.checked).map((box) => box.value);

  function make(tag, text, cls) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function clear(node) { if (node) node.replaceChildren(); }

  function issue(id, message, error = false) {
    const node = $(id);
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("ml-error", error);
  }

  function clampInt(val, min, max, fallback) {
    const n = Number.parseInt(String(val), 10);
    if (Number.isNaN(n)) return fallback;
    return Math.max(min, Math.min(max, n));
  }

  function updateLiveCalculator() {
    const bCount = selectedBenchmark().length;
    const aCount = approvedQuestions().length;
    const qCount = bCount + aCount;
    const mCount = checkedMarkets().length;
    const sCount = checkedModes().length;
    const repeats = clampInt($("ml-repeats")?.value, 1, 10, 1);
    const chunkSize = clampInt($("ml-chunk")?.value, 1, 10, 5);
    const budget = clampInt($("ml-budget")?.value, 1, 500, 250);
    const totalRequests = qCount * mCount * sCount * repeats;
    const totalChunks = totalRequests > 0 ? Math.ceil(totalRequests / chunkSize) : 0;

    if ($("ml-selected")) {
      $("ml-selected").textContent = `${bCount} benchmark + ${aCount} GSC approved = ${qCount} selected`;
    }
    if ($("ml-selected-detail")) {
      if (qCount === 0) {
        $("ml-selected-detail").textContent = "0 questions selected. Select benchmark questions or explicitly approve GSC draft questions above.";
      } else {
        const previewNames = chosen().slice(0, 3).map((q) => `"${q.text}"`).join(" · ");
        const more = qCount > 3 ? ` (+${qCount - 3} more)` : "";
        $("ml-selected-detail").textContent = `${qCount} total (${bCount} benchmark, ${aCount} approved GSC): ${previewNames}${more}`;
      }
    }
    if ($("ml-formula-text")) {
      const budgetNote = totalRequests > budget
        ? ` · Exceeds ${budget} request budget cap (increase budget or reduce scope; never silently truncated)`
        : ` · Within ${budget} request budget`;
      $("ml-formula-text").textContent =
        `${qCount} question${qCount === 1 ? "" : "s"} × ${mCount} market${mCount === 1 ? "" : "s"} × ${sCount} method${sCount === 1 ? "" : "s"} × ${repeats} repeat${repeats === 1 ? "" : "s"} = ${totalRequests} planned AI request${totalRequests === 1 ? "" : "s"} across ${totalChunks} processing chunk${totalChunks === 1 ? "" : "s"} (chunk size ${chunkSize})${budgetNote}`;
    }
    if ($("ml-live-math")) {
      $("ml-live-math").classList.toggle("ml-formula-warn", totalRequests > budget || totalRequests === 0);
    }
  }

  async function jsonPost(url, data) {
    const response = await fetch(url, {
      method: "POST", credentials: "same-origin", mode: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": app.dataset.csrf },
      body: JSON.stringify(data),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = result.detail;
      throw new Error(typeof detail === "string" ? detail : "The server could not process that request.");
    }
    return result;
  }

  function renderGscSnapshot(snapshot, draftCount) {
    const container = $("ml-gsc-results");
    if (!container || !snapshot) return;
    clear(container);
    container.hidden = false;

    const rows = Array.isArray(snapshot.rows) ? snapshot.rows : [];
    const eligibleCount = rows.filter((r) => r.status === "eligible").length;
    const brandCount = rows.filter((r) => r.status === "brand").length;
    const excludedCount = rows.filter((r) => r.status === "excluded_service_or_employment").length;

    const metricsRow = make("div", undefined, "ml-metrics");
    for (const [value, label] of [
      [snapshot.row_count ?? rows.length, "Queries imported"],
      [eligibleCount, "Eligible homeowner queries"],
      [snapshot.impressions ?? 0, "Row impressions"],
      [snapshot.clicks ?? 0, "Row clicks"],
      [draftCount, "Draft questions (unapproved)"],
      [brandCount + excludedCount, "Branded / excluded rows"],
    ]) {
      const metric = make("div", undefined, "ml-metric");
      metric.append(make("strong", String(value)), make("span", label));
      metricsRow.append(metric);
    }
    container.append(metricsRow);
    container.append(make("p", snapshot.note || "Imported query rows are traditional Google organic search data, not AI model rankings.", "ml-muted"));

    if (snapshot.invalid_count) {
      container.append(make("p", `${snapshot.invalid_count} invalid or duplicate row(s) were excluded. Check your file.`, "ml-error"));
      for (const entry of (snapshot.errors || []).slice(0, 5)) {
        container.append(make("p", `Row ${entry.row}: ${entry.reason}`, "ml-muted"));
      }
    }

    if (rows.length) {
      const details = make("details", undefined, "ml-question-details");
      details.open = true;
      details.append(make("summary", `Inspect imported GSC query rows (${rows.length})`));
      const wrap = make("div", undefined, "ml-table-wrap");
      const table = make("table", undefined, "ml-table");
      const thead = make("thead");
      const hr = make("tr");
      for (const col of ["Query", "Impressions", "Clicks", "Avg Pos", "Market / Geography", "Category", "Classification"]) {
        hr.append(make("th", col));
      }
      thead.append(hr);
      const tbody = make("tbody");
      for (const r of rows.slice(0, 100)) {
        const tr = make("tr");
        tr.append(
          make("td", r.query),
          make("td", String(r.impressions)),
          make("td", String(r.clicks)),
          make("td", String(r.avg_position)),
          make("td", r.geography || ""),
          make("td", r.category || ""),
        );
        const statusTd = make("td");
        const st = STATUS_LABELS[r.status] || { text: r.status || "unknown", cls: "ml-badge" };
        statusTd.append(make("span", st.text, st.cls));
        tr.append(statusTd);
        tbody.append(tr);
      }
      table.append(thead, tbody);
      wrap.append(table);
      details.append(wrap);
      container.append(details);
    }
  }

  function displaySuggestions() {
    clear(suggestionBox);
    if (!suggestions.length) {
      suggestionBox.append(make("p", "No eligible new homeowner question groups were found. Import and analyze GSC queries in Step 1 to generate reviewable draft questions.", "ml-muted"));
      updateLiveCalculator();
      return;
    }
    const approvedCount = approvedQuestions().length;
    const headRow = make("div", undefined, "ml-action-row ml-actions-wrap");
    headRow.append(
      make("h3", `${suggestions.length} draft GSC question proposals (${approvedCount} explicitly approved)`),
      make("span", "Drafts start unapproved. Check each question you want to include.", "ml-muted"),
    );
    suggestionBox.append(headRow);

    for (const item of suggestions) {
      const card = make("div", undefined, "ml-proposal-card");
      const label = make("label", undefined, "ml-proposal");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "ml-gsc-approve";
      check.checked = item.approved === true;
      check.addEventListener("change", () => {
        item.approved = check.checked;
        item.imported_from_json = false;
        statusBadge.textContent = item.approved ? "Approved in session" : "Draft — needs approval";
        statusBadge.className = item.approved ? "ml-badge" : "ml-badge ml-badge-warn";
        updateLiveCalculator();
      });

      const body = make("span", undefined, "ml-proposal-body");
      const titleRow = make("div", undefined, "ml-proposal-title");
      titleRow.append(make("strong", item.question));
      const badgeText = item.approved
        ? (item.imported_from_json ? "Imported JSON flag (unverified)" : "Approved in session")
        : "Draft — needs approval";
      const badgeCls = item.approved && !item.imported_from_json ? "ml-badge" : "ml-badge ml-badge-warn";
      const statusBadge = make("span", badgeText, badgeCls);
      titleRow.append(statusBadge);
      body.append(titleRow);

      const sourceQueries = Array.isArray(item.source_queries) ? item.source_queries : [];
      const meta = make(
        "small",
        `${item.category} · ${item.geography} · ${item.impressions} impressions · ${item.clicks ?? 0} clicks · ${sourceQueries.length} source quer${sourceQueries.length === 1 ? "y" : "ies"}`,
      );
      body.append(meta);

      if (sourceQueries.length) {
        const lineage = make("div", undefined, "ml-lineage");
        lineage.append(make("span", "Source query lineage: ", "ml-lineage-label"));
        for (const sq of sourceQueries) {
          lineage.append(
            make("span", `${sq.query} (${sq.impressions} impr, pos ${sq.avg_position})`, "ml-lineage-chip"),
          );
        }
        body.append(lineage);
      }

      label.append(check, body);
      card.append(label);
      suggestionBox.append(card);
    }
    updateLiveCalculator();
  }

  function renderPlanResults(plan, requestPayload) {
    const container = $("ml-plan-results");
    if (!container || !plan) return;
    clear(container);
    container.hidden = false;

    const row = make("div", undefined, "ml-metrics");
    for (const [value, label] of [
      [plan.question_count, "Selected questions"],
      [plan.market_count ?? requestPayload.markets.length, "Target markets"],
      [plan.mode_count, "Search methods"],
      [plan.repetitions ?? requestPayload.repetitions, "Repetitions"],
      [plan.request_count, "Planned AI requests"],
      [plan.chunk_count, `Processing chunks (size ${plan.chunk_size ?? requestPayload.chunk_size})`],
      [plan.provider_requests_executed ?? 0, "Provider calls executed"],
    ]) {
      const metric = make("div", undefined, "ml-metric");
      metric.append(make("strong", String(value)), make("span", label));
      row.append(metric);
    }
    container.append(row);

    container.append(
      make(
        "p",
        `Exact schedule: ${plan.question_count} questions × ${plan.market_count ?? requestPayload.markets.length} markets × ${plan.mode_count} methods × ${plan.repetitions ?? requestPayload.repetitions} repeat(s) = ${plan.request_count} total requests grouped into ${plan.chunk_count} processing chunks. Zero requests were truncated or sent to a live AI provider.`,
        "ml-muted",
      ),
    );

    if (Array.isArray(plan.warnings) && plan.warnings.length) {
      container.append(make("p", plan.warnings.join(" "), "ml-error"));
    }

    const previewItems = Array.isArray(plan.preview) ? plan.preview : [];
    if (previewItems.length) {
      container.append(make("h3", `Prompt preview (showing ${previewItems.length} of ${plan.request_count} planned requests)`));
      for (const item of previewItems) {
        const methodName = METHOD_LABELS[item.method] || item.method;
        container.append(
          make("div", `[${methodName} · ${item.market} · repeat #${item.repeat ?? 1}] ${item.prompt}`, "ml-prompt"),
        );
      }
    }
    container.append(make("p", plan.note || "Preview only: no Gemini calls sent, no credentials configured, no runs recorded.", "ml-muted"));
  }

  function buildDraftStateObject() {
    return {
      generated_at: new Date().toISOString(),
      type: "measuredfield.local_preview_plan",
      storage_policy: "explicit_file_export_only_no_localstorage",
      provider_calls_executed: 0,
      no_data_persistence: true,
      csv_text: $("ml-csv")?.value || "",
      gsc_snapshot: parsedSnapshot,
      suggestions: suggestions.map((item) => ({ ...item, approved: item.approved === true })),
      approved_proposals: suggestions.filter((item) => item.approved === true),
      selected_benchmark_ids: allBenchmarks.filter((b) => b.checked).map((b) => b.value),
      config: {
        markets: checkedMarkets(),
        modes: checkedModes(),
        repetitions: clampInt($("ml-repeats")?.value, 1, 10, 1),
        chunk_size: clampInt($("ml-chunk")?.value, 1, 10, 5),
        max_total: clampInt($("ml-budget")?.value, 1, 500, 250),
      },
      plan: latestPlan,
    };
  }

  function triggerJsonDownload(payload, filename) {
    const content = JSON.stringify(payload, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 500);
  }

  function restoreDraftFromJson(rawText) {
    let data;
    try {
      data = JSON.parse(rawText);
    } catch {
      throw new Error("Invalid JSON file. Could not parse local draft.");
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw new Error("Draft JSON must contain an object.");
    }
    // A JSON file is untrusted input, even when the owner chose to restore it.
    // Never treat its claimed approvals, results, or provider receipts as verified.
    if (data.type !== "measuredfield.local_preview_plan" || data.provider_calls_executed !== 0) {
      throw new Error("This is not a valid zero-provider-call MeasuredField draft.");
    }
    if (typeof data.csv_text !== "string" || data.csv_text.length > 320000) {
      throw new Error("Draft CSV text must be present and below 320 KB.");
    }
    if ($("ml-csv")) $("ml-csv").value = data.csv_text;
    // Do not reuse claims, source-query lineage, owner approval flags, or
    // generated results from an arbitrary JSON file. Re-analyze the restored
    // CSV and approve proposed questions again in the current session.
    parsedSnapshot = null;
    suggestions = [];
    latestPlan = null;
    if ($("ml-gsc-results")) {
      clear($("ml-gsc-results"));
      $("ml-gsc-results").hidden = true;
    }
    if ($("ml-plan-results")) {
      clear($("ml-plan-results"));
      $("ml-plan-results").hidden = true;
    }
    if ($("ml-export")) $("ml-export").disabled = true;

    if (Array.isArray(data.selected_benchmark_ids)) {
      const idSet = new Set(data.selected_benchmark_ids.map(String));
      allBenchmarks.forEach((box) => { box.checked = idSet.has(box.value); });
    } else if (data.plan && Array.isArray(data.plan.question_ids)) {
      const idSet = new Set(data.plan.question_ids.map(String));
      allBenchmarks.forEach((box) => { box.checked = idSet.has(box.value); });
    }

    if (data.config && typeof data.config === "object") {
      if (Array.isArray(data.config.markets) && data.config.markets.length) {
        const mSet = new Set(data.config.markets.map(String));
        allMarkets().forEach((box) => { box.checked = mSet.has(box.value); });
      }
      if (Array.isArray(data.config.modes) && data.config.modes.length) {
        const modeSet = new Set(data.config.modes.map(String));
        allModes().forEach((box) => { box.checked = modeSet.has(box.value); });
      }
      if ($("ml-repeats") && data.config.repetitions !== undefined) {
        $("ml-repeats").value = String(clampInt(data.config.repetitions, 1, 10, 1));
      }
      if ($("ml-chunk") && data.config.chunk_size !== undefined) {
        $("ml-chunk").value = String(clampInt(data.config.chunk_size, 1, 10, 5));
      }
      if ($("ml-budget") && data.config.max_total !== undefined) {
        $("ml-budget").value = String(clampInt(data.config.max_total, 1, 500, 250));
      }
    }

    displaySuggestions();
    issue("ml-gsc-message", "Local draft CSV restored. Analyze the rows again to verify the data and rebuild suggested questions.");
    issue("ml-plan-message", "Planning controls restored; reapprove GSC questions and build a fresh research plan.");
    updateLiveCalculator();
  }

  // Event listeners: Step 1 (GSC Import)
  $("ml-file")?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 320000) {
      issue("ml-gsc-message", "CSV exceeds this prototype's 320 KB limit.", true);
      return;
    }
    $("ml-csv").value = await file.text();
    issue("ml-gsc-message", `Loaded ${file.name}. Click Analyze GSC rows to inspect rows and generate draft questions.`);
  });

  $("ml-sample-csv")?.addEventListener("click", () => {
    $("ml-csv").value = SAMPLE_GSC_CSV;
    issue("ml-gsc-message", "Loaded synthetic sample GSC rows into the editor. Click Analyze GSC rows to parse.");
  });

  $("ml-clear-gsc")?.addEventListener("click", () => {
    $("ml-csv").value = "";
    if ($("ml-file")) $("ml-file").value = "";
    parsedSnapshot = null;
    suggestions = [];
    if ($("ml-gsc-results")) {
      clear($("ml-gsc-results"));
      $("ml-gsc-results").hidden = true;
    }
    displaySuggestions();
    issue("ml-gsc-message", "Cleared GSC CSV input and unapproved draft suggestions.");
  });

  $("ml-analyze")?.addEventListener("click", async () => {
    const button = $("ml-analyze");
    button.disabled = true;
    issue("ml-gsc-message", "Checking CSV columns and generating draft questions...");
    try {
      const result = await jsonPost(app.dataset.gscApi, { csv_text: $("ml-csv").value });
      parsedSnapshot = result.snapshot;
      // Never auto-approve imported suggestions; owner must explicitly check each proposal.
      suggestions = (result.suggestions || []).map((item) => ({ ...item, approved: false }));
      renderGscSnapshot(parsedSnapshot, suggestions.length);
      displaySuggestions();
      issue(
        "ml-gsc-message",
        `Imported ${parsedSnapshot.row_count} query rows (${parsedSnapshot.impressions} impressions, ${parsedSnapshot.clicks} clicks). Review and explicitly approve any of the ${suggestions.length} draft question groups in Step 2.`,
      );
    } catch (error) {
      issue("ml-gsc-message", error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  // Event listeners: Step 2 (Benchmark & explicit GSC approval selection)
  allBenchmarks.forEach((box) => box.addEventListener("change", updateLiveCalculator));

  $("ml-all")?.addEventListener("click", () => {
    allBenchmarks.forEach((box) => { box.checked = true; });
    updateLiveCalculator();
    issue("ml-plan-message", "All 22 benchmark questions selected.");
  });

  $("ml-none")?.addEventListener("click", () => {
    allBenchmarks.forEach((box) => { box.checked = false; });
    suggestions.forEach((item) => { item.approved = false; });
    displaySuggestions();
    updateLiveCalculator();
    issue("ml-plan-message", "Cleared all selected benchmark and GSC questions.");
  });

  $("ml-approved-only")?.addEventListener("click", () => {
    allBenchmarks.forEach((box) => { box.checked = false; });
    // Review and approval are strictly separate. Never auto-approve suggestions with a selection shortcut.
    displaySuggestions();
    updateLiveCalculator();
    const count = approvedQuestions().length;
    if (count === 0) {
      issue("ml-plan-message", "Benchmark cleared. 0 GSC questions are approved yet—explicitly check the draft GSC questions you approve above.", true);
    } else {
      issue("ml-plan-message", `Using ${count} explicitly approved GSC question(s) only.`);
    }
  });

  // Event listeners: Step 3 (Markets, methods, repetitions, chunk size, budget)
  allMarkets().forEach((box) => box.addEventListener("change", updateLiveCalculator));
  allModes().forEach((box) => box.addEventListener("change", updateLiveCalculator));
  ["ml-repeats", "ml-chunk", "ml-budget"].forEach((id) => {
    $(id)?.addEventListener("input", updateLiveCalculator);
    $(id)?.addEventListener("change", updateLiveCalculator);
  });

  $("ml-all-markets")?.addEventListener("click", () => {
    allMarkets().forEach((box) => { box.checked = true; });
    updateLiveCalculator();
  });

  $("ml-one-market")?.addEventListener("click", () => {
    allMarkets().forEach((box, idx) => { box.checked = idx === 0; });
    updateLiveCalculator();
  });

  $("ml-all-modes")?.addEventListener("click", () => {
    allModes().forEach((box) => { box.checked = true; });
    updateLiveCalculator();
  });

  $("ml-one-mode")?.addEventListener("click", () => {
    allModes().forEach((box) => { box.checked = box.value === "as_written"; });
    updateLiveCalculator();
  });

  $("ml-plan")?.addEventListener("click", async () => {
    const questions = chosen().map((q) => ({ id: q.id, text: q.text }));
    const modes = checkedModes();
    const markets = checkedMarkets();
    const repetitions = clampInt($("ml-repeats")?.value, 1, 10, 1);
    const chunk_size = clampInt($("ml-chunk")?.value, 1, 10, 5);
    const max_total = clampInt($("ml-budget")?.value, 1, 500, 250);

    if (!questions.length) {
      issue("ml-plan-message", "Select at least one benchmark or explicitly approved GSC question in Step 2.", true);
      return;
    }
    if (!markets.length) {
      issue("ml-plan-message", "Select at least one target market checkbox.", true);
      return;
    }
    if (!modes.length) {
      issue("ml-plan-message", "Select at least one search method checkbox.", true);
      return;
    }

    const body = { questions, modes, markets, repetitions, chunk_size, max_total };
    issue("ml-plan-message", "Calculating complete multi-market research plan...");
    try {
      latestPlan = await jsonPost(app.dataset.planApi, body);
      latestPlan.question_ids = questions.map((q) => q.id);
      latestPlan.markets = markets;
      latestPlan.modes = modes;
      renderPlanResults(latestPlan, body);
      if ($("ml-export")) $("ml-export").disabled = false;
      issue(
        "ml-plan-message",
        `${latestPlan.request_count} planned requests across ${markets.length} market(s) and ${modes.length} method(s) in ${latestPlan.chunk_count} processing chunks. 0 provider calls sent. Export your plan below.`,
      );
    } catch (error) {
      issue("ml-plan-message", error.message, true);
      if ($("ml-export")) $("ml-export").disabled = true;
    }
  });

  // Event listeners: Step 4 (Safe JSON Export / Import without localStorage)
  $("ml-export")?.addEventListener("click", () => {
    if (!latestPlan) return;
    triggerJsonDownload(buildDraftStateObject(), "measuredfield-visibility-research-plan.json");
    issue("ml-draft-message", "Exported research plan JSON. No browser localStorage was written.");
  });

  $("ml-export-draft")?.addEventListener("click", () => {
    triggerJsonDownload(buildDraftStateObject(), "measuredfield-visibility-draft-state.json");
    issue("ml-draft-message", "Exported current local draft state as JSON.");
  });

  $("ml-import-file")?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 512000) {
      issue("ml-draft-message", "Draft JSON exceeds the 512 KB safety limit.", true);
      return;
    }
    try {
      const text = await file.text();
      restoreDraftFromJson(text);
      issue("ml-draft-message", `Restored unverified local JSON draft from ${file.name}. Review imported selections; 0 live provider calls executed.`);
    } catch (error) {
      issue("ml-draft-message", error.message, true);
    }
  });

  updateLiveCalculator();
})();
