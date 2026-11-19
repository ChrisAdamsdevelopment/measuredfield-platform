/* MF-049: client-only presentation; no client-side trust elevation or storage. */
(() => {
  "use strict";
  const app = document.getElementById("lv-app");
  if (!app) return;
  const $ = (id) => document.getElementById(id);
  const node = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = String(text);
    if (className) item.className = className;
    return item;
  };
  const add = (parent, tag, text, className) => {
    const child = node(tag, text, className);
    parent.appendChild(child);
    return child;
  };
  const fmt = (value) => value === null || value === undefined ? "Not measured" : String(value);
  const percent = (value) => value === null || value === undefined ? "Not measured" : Math.round(value * 100) + "%";
  const status = (message, error = false) => {
    $("lv-status").textContent = message;
    $("lv-status").classList.toggle("lv-error", error);
  };
  const clearResults = () => {
    $("lv-results").hidden = true;
    ["lv-stats", "lv-markets", "lv-actions", "lv-limitations"].forEach(id => $(id).replaceChildren());
  };

  function renderSummary(result) {
    const stats = [
      [result.markets.length, "Cities compared"],
      [result.unique_samples, "Distinct coordinate/query samples"],
      [result.records_imported, "CSV rows inspected"],
    ];
    for (const [value, label] of stats) {
      const box = add($("lv-stats"), "div", undefined, "lv-stat");
      add(box, "strong", value);
      add(box, "span", label);
    }
  }

  function renderGeographicPlot(card, samples) {
    // Schematic coordinate plot; not a basemap or claim of Google GPS behavior.
    if (samples.length < 2) return;
    const latitudes = samples.map(p => p.lat);
    const longitudes = samples.map(p => p.lon);
    const minLat = Math.min(...latitudes), maxLat = Math.max(...latitudes);
    const minLon = Math.min(...longitudes), maxLon = Math.max(...longitudes);
    const spanLat = Math.max(0.002, maxLat - minLat);
    const spanLon = Math.max(0.002, maxLon - minLon);
    const canvas = add(card, "div", undefined, "lv-coordinate-map");
    add(canvas, "strong", "Geographic sampling pattern");
    add(canvas, "p", "Schematic plot from imported coordinates, not a street map. Dots show source-reported positions.");
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 320 206");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Geographic placement of sampled search positions. Detailed source values follow below.");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const line = (x1, y1, x2, y2) => {
      const el = document.createElementNS(ns, "line");
      for (const [key, value] of Object.entries({x1,y1,x2,y2})) el.setAttribute(key, String(value));
      el.setAttribute("stroke", "#d6e4dd");
      el.setAttribute("stroke-width", "1");
      svg.appendChild(el);
    };
    for (let i = 0; i <= 4; i++) {
      const tick = 20 + i * 70;
      line(tick, 14, tick, 184);
      line(20, 14 + i * 42.5, 300, 14 + i * 42.5);
    }
    for (const point of samples.slice(0, 100)) {
      const x = 20 + ((point.lon - minLon) / spanLon) * 280;
      const y = 184 - ((point.lat - minLat) / spanLat) * 170;
      const r = point.position;
      const circle = document.createElementNS(ns, "circle");
      circle.setAttribute("cx", String(x));
      circle.setAttribute("cy", String(y));
      circle.setAttribute("r", "7");
      circle.setAttribute("stroke", "#fff");
      circle.setAttribute("stroke-width", "2");
      circle.setAttribute("fill", r === null ? "#829397" : r <= 3 ? "#198763" : r <= 10 ? "#b49a37" : "#c26b45");
      circle.setAttribute("fill-opacity", ".85");
      const label = document.createElementNS(ns, "title");
      label.textContent = point.keyword + ": " + (r === null ? "rank unknown" : "rank " + r)
        + " / " + point.surface + " / " + point.lat + ", " + point.lon;
      circle.appendChild(label);
      svg.appendChild(circle);
    }
    canvas.appendChild(svg);
    const legend = add(canvas, "div", undefined, "lv-geo-legend");
    for (const [cls, text] of [["lv-top", "1–3"], ["lv-mid", "4–10"], ["lv-low", "11–100"], ["lv-none", "Unknown"]]) {
      const chip = add(legend, "span", text, cls);
      chip.setAttribute("title", "Owner-imported rank sample: " + text);
    }
  }

  function renderMarket(market) {
    const card = add($("lv-markets"), "article", undefined, "lv-market");
    const header = add(card, "header");
    const heading = add(header, "div");
    add(heading, "h3", market.city);
    add(heading, "small", market.samples + " sampled location/query results");
    const rank = add(header, "div", undefined, "lv-rank");
    add(rank, "strong", fmt(market.average_position));
    add(rank, "span", "Mean imported position");
    const facts = add(card, "div", undefined, "lv-market-facts");
    add(facts, "span", "Top 3: " + percent(market.top3_share));
    add(facts, "span", "Top 10: " + percent(market.top10_share));
    add(facts, "span", "Unknown: " + market.unknown_samples);
    renderGeographicPlot(card, market.points);
    const points = add(card, "div", undefined, "lv-points");
    points.setAttribute("aria-label", "Sampled ranks and approximate source coordinates");
    for (const point of market.points.slice(0, 18)) {
      const value = point.position;
      const cls = value === null ? "lv-none" : value <= 3 ? "lv-top" : value <= 10 ? "lv-mid" : "lv-low";
      const sample = add(points, "div", undefined, "lv-point " + cls);
      add(sample, "strong", value === null ? "?" : "#" + value);
      add(sample, "span", point.lat + ", " + point.lon);
      sample.title = point.keyword + " / " + point.surface + " / " + point.checked_at + " / " + point.provider;
    }
    if (market.points.length > 18) {
      add(card, "p", "Showing the first 18 of " + market.points.length + " points. Summary includes all imported samples.", "lv-rivals");
    }
    if (market.competitors.length) {
      const rivals = add(card, "div", undefined, "lv-rivals");
      add(rivals, "strong", "Observed competitor comparisons");
      for (const competitor of market.competitors.slice(0, 4)) {
        add(rivals, "p", competitor.name + " ahead in " + competitor.ahead_count +
          " of " + competitor.paired_samples + " comparable samples.");
      }
    } else {
      add(card, "p", "No comparable competitor positions supplied for this city.", "lv-rivals");
    }
  }

  function renderAction(action, index) {
    const card = add($("lv-actions"), "li", undefined, "lv-action");
    add(card, "div", "Priority " + action.priority + " · Step " + (index + 1), "lv-priority");
    add(card, "h3", action.title);
    add(card, "p", action.reason);
    add(card, "b", "Consider doing this");
    add(card, "p", action.next_step);
    add(card, "div", action.confidence === "needs_more_evidence" ?
      "More comparable evidence needed" : "Derived from owner-imported observations only", "lv-action-foot");
  }

  function render(result) {
    clearResults();
    renderSummary(result);
    result.markets.forEach(renderMarket);
    result.recommendations.forEach(renderAction);
    result.limitations.forEach(x => add($("lv-limitations"), "li", x));
    $("lv-results").hidden = false;
    $("lv-results").scrollIntoView({behavior: "smooth", block: "start"});
  }

  $("lv-file").addEventListener("change", async (event) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    if (file.size > 320000) {
      status("The report is too large; use up to 320 KB.", true);
      return;
    }
    try {
      $("lv-csv").value = await file.text();
      clearResults();
      status("CSV loaded locally. Review its source, then choose Compare locations.");
    } catch {
      status("Could not read this CSV file.", true);
    }
  });

  $("lv-clear").addEventListener("click", () => {
    $("lv-file").value = "";
    $("lv-csv").value = "";
    clearResults();
    status("No report loaded yet. Start with a real provider export.");
  });

  $("lv-analyze").addEventListener("click", async () => {
    const csv = $("lv-csv").value;
    if (!csv.trim()) {
      status("Add a CSV export before comparing locations.", true);
      return;
    }
    if (new Blob([csv]).size > 320000) {
      status("The CSV exceeds the 320 KB limit.", true);
      return;
    }
    const button = $("lv-analyze");
    button.disabled = true;
    clearResults();
    status("Checking the format and comparing imported observations...");
    try {
      const response = await fetch("/api/v1/modules/local-search/analyze", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": app.dataset.csrf || "",
        },
        body: JSON.stringify({csv_text: csv}),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || payload.error || "The import was not accepted.");
      }
      if (payload.status !== "owner_imported_preview_not_verified" ||
          payload.provider_calls_made !== 0 || !Array.isArray(payload.markets)) {
        throw new Error("The source status could not be confirmed.");
      }
      render(payload);
      status("Comparison ready. Imported observations are not independently verified.");
    } catch (err) {
      status(err?.message || "The report could not be analyzed.", true);
    } finally {
      button.disabled = false;
    }
  });
})();
