/* Honest, accessible website screenshot comparison and task suggestions.
   This script never performs hidden API calls or impersonates ChatGPT.
   Real task submission uses standard CSRF-protected server forms. */
(() => {
  "use strict";
  const root = document.querySelector(".mf-studio");
  if (!root) return;
  const comparison = root.querySelector("[data-mf-compare]");
  const slider = root.querySelector("#mf-studio-range");
  const compareState = root.querySelector("#mf-studio-compare-state");
  if (comparison && slider) {
    const update = () => {
      const before = Math.max(0, Math.min(100, Number(slider.value)));
      comparison.style.setProperty("--mf-compare", before + "%");
      slider.setAttribute("aria-valuetext",
        before + " percent before; " + (100 - before) + " percent after");
      if (compareState) compareState.textContent =
        before + "% before / " + (100 - before) + "% after";
    };
    slider.addEventListener("input", update);
    slider.addEventListener("change", update);
    update();
  }
  const viewport = root.querySelector("[data-mf-viewport]");
  const viewButtons = root.querySelectorAll("[data-mf-preview]");
  viewButtons.forEach((button) => button.addEventListener("click", () => {
    const view = button.getAttribute("data-mf-preview");
    if (!viewport || (view !== "desktop" && view !== "mobile")) return;
    viewport.setAttribute("data-mf-viewport", view);
    viewButtons.forEach((item) => {
      const active = item === button;
      item.setAttribute("aria-pressed", String(active));
      item.classList.toggle("is-current", active);
    });
  }));
  const editor = root.querySelector("#mf-studio-request");
  root.querySelectorAll("[data-mf-suggest]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!editor) return;
      editor.value = button.getAttribute("data-mf-suggest") || "";
      editor.focus();
    });
  });
})();