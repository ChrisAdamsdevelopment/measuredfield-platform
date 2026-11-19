/* MF-053: owner-first growth journey; safe DOM, no secrets/storage/provider calls. */
(() => {
  "use strict";
  const app = document.getElementById("gj");
  if (!app) return;
  const byId = id => document.getElementById(id);
  const node = (tag, value, className) => {
    const element = document.createElement(tag);
    if (typeof value !== "undefined") element.textContent = String(value);
    if (className) element.className = className;
    return element;
  };
  const append = (parent, tag, value, className) => {
    const element = node(tag, value, className);
    parent.appendChild(element);
    return element;
  };
  // Never follow a URL from a returned JSON payload unless it is known to be
  // an existing owner-only MeasuredField route. Nothing auto-executes.
  const supportedLinks = new Set([
    "/app/website", "/app/modules/visibility", "/app/modules/local-search",
    "/app/inquiries", "/app/lead-rescue", "/app/improvements", "/app/connections",
  ]);
  function labelStatus(message, error = false) {
    const target = byId("gj-status");
    target.textContent = message;
    target.classList.toggle("gj-error", error);
  }
  function choices(form, name) {
    return Array.from(form.querySelectorAll("input[name=" + name + "]:checked"))
      .map(input => input.value);
  }
  function renderProvider(provider) {
    byId("gj-provider-title").textContent = provider.title;
    byId("gj-provider-detail").textContent = provider.what_works_now + " " + provider.cost;
    byId("gj-provider-privacy").textContent = provider.privacy;
    byId("gj-provider-state").textContent = provider.configuration === "no_external_provider_required"
      ? "No connection needed" : "Not connected or verified";
  }
  function renderStep(step) {
    const row = append(byId("gj-steps"), "li", undefined, "gj-step");
    const badge = append(row, "span", String(step.number).padStart(2, "0"), "gj-step-badge");
    badge.setAttribute("aria-hidden", "true");
    const body = append(row, "div", undefined, "gj-step-body");
    append(body, "h4", step.title);
    append(body, "p", step.why);
    append(body, "p", step.recommended_action, "gj-step-action");
    const meta = append(body, "div", undefined, "gj-step-meta");
    append(meta, "span", "Effort: " + step.effort);
    append(meta, "span", "Needs owner approval");
    if (step.owner_reports_existing_account !== null) {
      append(meta, "span", step.owner_reports_existing_account ?
        "Account reported · not connected" : "Account not yet reported");
    }
    const measure = append(body, "div", undefined, "gj-step-measure");
    append(measure, "strong", "Measure ");
    append(measure, "span", step.measure);
    if (step.prerequisites.length) {
      append(body, "p", "Helpful first: " + step.prerequisites.join(" · "), "gj-prereqs");
    }
    if (step.link && supportedLinks.has(step.link)) {
      const link = append(body, "a", "Open related tool ›", "gj-step-link");
      link.href = step.link;
      link.setAttribute("aria-label", "Open related tool for " + step.title);
    }
  }
  function renderPlan(plan) {
    byId("gj-results").hidden = false;
    byId("gj-result-context").textContent =
      "For " + plan.business.name + " in " + plan.business.service_area +
      ". Your actions are proposals until reviewed and completed.";
    const stats = byId("gj-result-stats");
    stats.replaceChildren();
    const metrics = [
      [plan.planned_steps, "recommended steps"],
      [plan.provider.model_calls_executed, "AI calls made"],
      [0, "accounts linked by this plan"],
    ];
    for (const [number, label] of metrics) {
      const card = append(stats, "div", undefined, "gj-stat");
      append(card, "strong", number);
      append(card, "small", label);
    }
    renderProvider(plan.provider);
    byId("gj-steps").replaceChildren();
    plan.steps.forEach(renderStep);
    byId("gj-opportunity").textContent = plan.industry_opportunity.hypothesis;
    byId("gj-opportunity-measure").textContent =
      "Check for demand using " + plan.industry_opportunity.measure + ".";
    byId("gj-limits").replaceChildren();
    plan.evidence_limits.forEach(value => append(byId("gj-limits"), "li", value));
  }
  byId("gj-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const selectedGoals = choices(form, "goals");
    if (!selectedGoals.length) {
      labelStatus("Choose at least one goal to build a useful plan.", true);
      form.querySelector("input[name=goals]").focus();
      return;
    }
    const payload = {
      business_name: byId("gj-name").value,
      service_area: byId("gj-area").value,
      industry: byId("gj-industry").value,
      website_state: byId("gj-website").value,
      goals: selectedGoals,
      ai_mode: form.querySelector("input[name=ai_mode]:checked").value,
      owner_has_accounts: choices(form, "owner_has_accounts"),
    };
    const button = byId("gj-generate");
    button.disabled = true;
    byId("gj-results").hidden = true;
    labelStatus("Building your private plan from the choices above...");
    try {
      const response = await fetch("/api/v1/growth-start/plan", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": app.dataset.csrf || "",
        },
        body: JSON.stringify(payload),
      });
      const plan = await response.json();
      if (!response.ok) throw new Error(plan.detail || plan.error || "This plan was not accepted.");
      if (plan.status !== "local_planning_preview_no_automated_actions" ||
          plan.provider?.key_entry_enabled !== false ||
          plan.provider?.model_calls_executed !== 0 ||
          !Array.isArray(plan.steps)) {
        throw new Error("The plan's safe preview status could not be confirmed.");
      }
      renderPlan(plan);
      labelStatus("Your plan is ready. No accounts were connected, and no AI, email, publishing or paid actions took place.");
      byId("gj-results").scrollIntoView({behavior: "smooth", block: "start"});
    } catch (error) {
      labelStatus(typeof error?.message === "string" ?
        error.message : "We couldn't build the plan. Please try again.", true);
    } finally {
      button.disabled = false;
    }
  });
})();
