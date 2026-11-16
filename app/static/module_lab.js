/* MeasuredField AI Visibility Lab: private local preview, no external provider calls. */
(() => {
  "use strict";
  const app = document.getElementById("mf-lab");
  if (!app) return;
  const $ = (id) => document.getElementById(id);
  const allBenchmarks = Array.from(document.querySelectorAll(".ml-benchmark"));
  const suggestionBox = $("ml-suggestions");
  let suggestions = [];
  let parsedSnapshot = null;
  let latestPlan = null;

  const selectedBenchmark = () => allBenchmarks.filter((box) => box.checked).map((box) => ({
    id: box.value, text: box.dataset.text,
  }));
  const approvedQuestions = () => suggestions.filter((item) => item.approved).map((item) => ({
    id: item.id, text: item.question,
  }));
  const chosen = () => [...selectedBenchmark(), ...approvedQuestions()];

  function countSelected() {
    const n = selectedBenchmark().length;
    const a = approvedQuestions().length;
    $("ml-selected").textContent = `${n} benchmark + ${a} GSC approved = ${n + a} selected`;
  }
  function make(tag, text, cls) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function clear(node) { node.replaceChildren(); }
  async function jsonPost(url, data) {
    const response = await fetch(url, {
      method: "POST", credentials: "same-origin", mode: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": app.dataset.csrf},
      body: JSON.stringify(data),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = result.detail;
      throw new Error(typeof detail === "string" ? detail : "The server could not process that request.");
    }
    return result;
  }
  function issue(id, message, error = false) {
    const node = $(id);
    node.textContent = message;
    node.classList.toggle("ml-error", error);
  }

  allBenchmarks.forEach((box) => box.addEventListener("change", countSelected));
  $("ml-all").addEventListener("click", () => {
    allBenchmarks.forEach((box) => { box.checked = true; });
    countSelected();
  });
  $("ml-none").addEventListener("click", () => {
    allBenchmarks.forEach((box) => { box.checked = false; });
    countSelected();
  });
  $("ml-approved-only").addEventListener("click", () => {
    allBenchmarks.forEach((box) => { box.checked = false; });
    // Review and approval are separate. Never approve suggestions with a selection shortcut.
    displaySuggestions();
    countSelected();
    issue("ml-plan-message", "Using only approved GSC questions. Choose research methods and build the plan.");
  });
  $("ml-file").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 320000) {
      issue("ml-gsc-message", "CSV exceeds this prototype's 320 KB limit.", true);
      return;
    }
    $("ml-csv").value = await file.text();
    issue("ml-gsc-message", `Loaded ${file.name}. Click Analyze GSC rows to see the actual data.`);
  });
  function displaySuggestions() {
    clear(suggestionBox);
    if (!suggestions.length) {
      suggestionBox.append(make("p", "No eligible new homeowner question groups were found. Review your imported queries.", "ml-muted"));
      return;
    }
    suggestionBox.append(make("h3", `${suggestions.length} suggested research questions`));
    suggestionBox.append(make("p", "Select the questions you approve for a future experiment. Nothing is sent to Gemini.", "ml-muted"));
    for (const [i, item] of suggestions.entries()) {
      const label = make("label", undefined, "ml-proposal");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = item.approved === true;
      check.addEventListener("change", () => {
        item.approved = check.checked;
        countSelected();
      });
      const body = make("span");
      body.append(make("strong", item.question));
      const meta = make("small", `${item.category} · ${item.geography} · ${item.impressions} impressions · ${item.source_queries.length} source queries`);
      body.append(meta);
      label.append(check, body);
      suggestionBox.append(label);
    }
    countSelected();
  }
  $("ml-analyze").addEventListener("click", async () => {
    const button = $("ml-analyze");
    button.disabled = true;
    issue("ml-gsc-message", "Checking CSV columns and generating draft questions...");
    try {
      const result = await jsonPost(app.dataset.gscApi, {csv_text: $("ml-csv").value});
      parsedSnapshot = result.snapshot;
      suggestions = result.suggestions.map((item) => ({...item, approved: false}));
      const container = $("ml-gsc-results"); clear(container); container.hidden = false;
      const row = make("div", undefined, "ml-metrics");
      for (const [value, label] of [
        [parsedSnapshot.row_count, "Queries imported"],
        [parsedSnapshot.impressions, "Row impressions"],
        [parsedSnapshot.clicks, "Row clicks"],
        [result.suggestions.length, "Draft questions"],
      ]) {
        const metric = make("div", undefined, "ml-metric");
        metric.append(make("strong", String(value)), make("span", label));
        row.append(metric);
      }
      container.append(row);
      container.append(make("p", parsedSnapshot.note, "ml-muted"));
      if (parsedSnapshot.invalid_count) {
        container.append(make("p", `${parsedSnapshot.invalid_count} invalid/duplicate rows were excluded. Check your file.`, "ml-error"));
        for (const entry of parsedSnapshot.errors.slice(0, 3)) {
          container.append(make("p", `Row ${entry.row}: ${entry.reason}`, "ml-muted"));
        }
      }
      displaySuggestions();
      issue("ml-gsc-message", `Imported ${parsedSnapshot.row_count} query rows. Review ${suggestions.length} new question groups below.`);
    } catch (error) {
      issue("ml-gsc-message", error.message, true);
    } finally { button.disabled = false; }
  });

  $("ml-plan").addEventListener("click", async () => {
    const questions = chosen();
    const modes = Array.from(document.querySelectorAll('input[name="ml-mode"]:checked')).map((box) => box.value);
    const markets = [$("ml-market").value];
    const body = {
      questions, modes, markets,
      repetitions: Number($("ml-repeats").value),
      chunk_size: Number($("ml-chunk").value),
      max_total: Number($("ml-budget").value),
    };
    issue("ml-plan-message", "Calculating every requested question and method...");
    try {
      latestPlan = await jsonPost(app.dataset.planApi, body);
      latestPlan.question_ids = questions.map((q) => q.id);
      const container = $("ml-plan-results"); clear(container); container.hidden = false;
      const row = make("div", undefined, "ml-metrics");
      for (const [value, label] of [
        [latestPlan.question_count, "Questions"],
        [latestPlan.mode_count, "Methods"],
        [latestPlan.request_count, "Planned AI requests"],
        [latestPlan.chunk_count, "Processing chunks"],
      ]) {
        const metric = make("div", undefined, "ml-metric");
        metric.append(make("strong", String(value)), make("span", label));
        row.append(metric);
      }
      container.append(row);
      container.append(make("h3", "Prompt preview"));
      for (const item of latestPlan.preview) {
        container.append(make("div", `[${item.method} / ${item.market}] ${item.prompt}`, "ml-prompt"));
      }
      container.append(make("p", latestPlan.note, "ml-muted"));
      if (latestPlan.warnings.length) container.append(make("p", latestPlan.warnings.join(" "), "ml-error"));
      $("ml-export").disabled = false;
      issue("ml-plan-message", `${latestPlan.request_count} requests in ${latestPlan.chunk_count} chunks. None sent. Export your plan below.`);
    } catch (error) {
      issue("ml-plan-message", error.message, true);
      $("ml-export").disabled = true;
    }
  });

  $("ml-export").addEventListener("click", () => {
    if (!latestPlan) return;
    const content = JSON.stringify({
      generated_at: new Date().toISOString(), type: "measuredfield.local_preview_plan",
      provider_calls_executed: 0, gsc_snapshot: parsedSnapshot,
      approved_proposals: suggestions.filter((item) => item.approved),
      plan: latestPlan, no_data_persistence: true,
    }, null, 2);
    const url = URL.createObjectURL(new Blob([content], {type: "application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = "measuredfield-visibility-research-plan.json";
    document.body.append(link);
    link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 500);
  });
  countSelected();
})();
