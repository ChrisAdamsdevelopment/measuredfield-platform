/* Owner-facing website workbench: a real, keyboard-accessible snapshot comparator.
 * No network calls, DOM HTML injection, AI impersonation, or automatic publishing.
 */
(() => {
  "use strict";

  const root = document.querySelector(".mf-studio");
  if (!root) return;

  const comparison = root.querySelector("[data-mf-compare]");
  const slider = root.querySelector("#mf-studio-range");
  const compareState = root.querySelector("#mf-studio-compare-state");

  if (comparison && slider) {
    const updateSlider = () => {
      const before = Math.max(0, Math.min(100, Number(slider.value)));
      comparison.style.setProperty("--mf-compare", before + "%");
      slider.setAttribute("aria-valuetext",
        before + " percent of the before website; " + (100 - before) + " percent of the after website");
      if (compareState) {
        compareState.textContent = before + "% before / " + (100 - before) + "% after";
      }
    };
    slider.addEventListener("input", updateSlider);
    slider.addEventListener("change", updateSlider);
    updateSlider();
  }

  const viewport = root.querySelector("[data-mf-viewport]");
  const viewButtons = root.querySelectorAll("[data-mf-preview]");
  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.getAttribute("data-mf-preview");
      if (!viewport || (view !== "desktop" && view !== "mobile")) return;
      viewport.setAttribute("data-mf-viewport", view);
      viewButtons.forEach((item) => {
        const active = item === button;
        item.setAttribute("aria-pressed", String(active));
        item.classList.toggle("is-current", active);
      });
    });
  });

  const editor = root.querySelector("#mf-studio-request");
  const feedback = root.querySelector("#mf-studio-copy-feedback");
  const copyButton = root.querySelector("#mf-studio-copy");
  root.querySelectorAll("[data-mf-suggest]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!editor) return;
      editor.value = button.getAttribute("data-mf-suggest") || "";
      editor.focus();
      if (feedback) feedback.textContent = "Change the request if needed, then copy it for ChatGPT.";
    });
  });

  const compose = (request) => {
    const business = root.getAttribute("data-business-name") || "my business";
    const id = root.getAttribute("data-business-id") || "";
    return [
      "Please work on my MeasuredField business: " + business + ".",
      id ? "Business ID: " + id + "." : "",
      "My request: " + request,
      "",
      "Use my connected MeasuredField tools and check my currently authorized scopes.",
      "Please inspect the current website and latest saved/draft versions before editing.",
      "Where write tools are authorized, perform real changes in approved projects,",
      "check work receipts, and report actual outcomes and blockers.",
      "Do not invent completed work or metrics. Do not claim publishing or customer contact",
      "unless the relevant authorized tool actually confirms it. Never bypass owner limits.",
      "If the necessary action is not yet supported, explain what connection or permission is missing."
    ].filter(Boolean).join("\n");
  };

  if (copyButton && editor) {
    copyButton.addEventListener("click", async () => {
      const request = editor.value.trim();
      if (!request) {
        editor.focus();
        if (feedback) feedback.textContent = "Describe the website change you want first.";
        return;
      }
      const prompt = compose(request);
      let copied = false;
      try {
        if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
          await navigator.clipboard.writeText(prompt);
          copied = true;
        }
      } catch (_) {
        // Some browsers restrict clipboard permission. Provide a selectable fallback.
      }
      if (!copied) {
        const temp = document.createElement("textarea");
        temp.value = prompt;
        temp.setAttribute("readonly", "");
        temp.style.position = "fixed";
        temp.style.opacity = "0";
        document.body.appendChild(temp);
        temp.select();
        try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
        temp.remove();
      }
      if (feedback) {
        feedback.textContent = copied
          ? "Request copied. Open ChatGPT, choose the MeasuredField connection and paste to start real authorized work."
          : "Clipboard permission was blocked. Select and copy your request from the box, then open ChatGPT.";
      }
    });
  }
})();
