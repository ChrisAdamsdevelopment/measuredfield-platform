"use strict";
// No tracking, no external network: copies an already-visible Stripe promo code.
// Hidden by the template until a real Stripe Price and offer disclosure exist.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-copy-promo]");
  if (!button) return;
  const code = button.dataset.copyPromo || "";
  const row = button.closest(".promo-row");
  const status = row && row.querySelector(".promo-status");
  if (!/^[A-Za-z0-9_-]{3,48}$/.test(code)) {
    if (status) status.textContent = "This code is unavailable.";
    return;
  }
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(code);
      ok = true;
    } else {
      const field = document.createElement("textarea");
      field.value = code;
      field.readOnly = true;
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      ok = document.execCommand("copy");
      field.remove();
    }
  } catch (_error) {
    ok = false;
  }
  if (status) status.textContent = ok ? "Copied! Paste it into Stripe Checkout." :
    "Copy was unavailable. Select the code and copy it manually.";
  if (ok) button.textContent = "Copied";
});
