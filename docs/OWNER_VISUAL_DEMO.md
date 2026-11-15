# MeasuredField Owner-Branded Visual Demo Studio (MF-016)

This document describes how to launch and exercise the development-only **Owner-Branded Visual Preview Studio** inside the canonical MeasuredField application (`mf_platform`).

---

## 1. Local Launch Command & URL

From `D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\mf_platform`:

```powershell
$env:MF_ENV = "development"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open in a browser:

```text
http://localhost:8000/dev/visual-preview
```

---

## 2. Three-Stage Interactive Demonstration Flow

### Stage 1 — Static HTML Diagnostics & Brand Asset Discovery
- Visiting `GET /dev/visual-preview` (or submitting `POST /dev/visual-preview/inspect`) runs `MF-014` static HTML diagnostics (`inspect_public_url`) and `MF-015` brand asset discovery (`extract_asset_candidates` + `verify_and_classify_assets`) plus evidence-linked visual proposal generation (`build_deterministic_visual_proposal`).
- You can choose either:
  1. **Built-In Synthetic Contractor Fixture** (`Granite Peak Kitchen & Bath Remodeling`, completely offline-safe with synthetic PNG logo and EXIF-tagged JPEG hero photo), or
  2. **Public `http(s)://` Website URL** (inspected through `MF-014`'s SSRF-pinned public-IP transport).
- **No third-party images are copied or stored during Stage 1.** Candidate assets appear as unconfirmed metadata cards until the owner explicitly attests usage rights.
- **Honest Before-Pane Framing:** The Before pane is explicitly labeled `BEFORE: Observed Static HTML Diagnostics & Discovered Asset Metadata` and states that it presents static HTML diagnostic evidence rather than a live visual screenshot of the original website.

### Stage 2 — Owner Rights Attestation & Safe Raster Re-Encoding
- In the Stage 2 card, select a discovered **Header Logo Candidate** and/or **Hero Photograph Candidate**, check the required owner rights confirmation box (`owner_rights_confirmed="yes"`), and click **"Confirm Owner Rights & Render Real Brand Media in After Page"** (`POST /dev/visual-preview/attest`).
- MeasuredField verifies the CSRF token and explicit attestation (`OwnerMediaAttestation(owner_attested_rights=True)`), downloads the selected asset through `PinnedPublicIPTransport` (max 2 validated redirect hops, max 2 MiB download), verifies raster magic bytes (`PNG`, `JPEG`, `WEBP` only — rejecting `SVG`, `HTML`, `XML`, `ICO`, and `GIF`), enforces pixel limits (`<= 12 MP`, `<= 4096px`), copies pixels onto a fresh Pillow canvas to strip all EXIF/ICC/XMP metadata, and re-encodes clean raster bytes into the expiring `PreviewMediaStore`.

### Stage 3 — Interactive Before / After Visual Studio
- After Stage 2 completes, the After pane renders the business's redesigned concept website with:
  - Real `<img class="mf-authorized-brand-img" src="/dev/visual-preview/media/<session_id>/<media_token>">` elements served strictly from MeasuredField's same-origin temporary media endpoint (`img-src 'self' data:`).
  - Comparison View Toolbar: **Split Compare (Before & After)**, **Before: Static HTML Diagnostics & Assets**, and **After: Interactive Redesign Concept**.
  - Viewport Switcher: **Desktop** and **Mobile (375px)**.
  - **Multi-Change Reversible Component Toggles:** Each proposed improvement can be toggled on/off independently. Multiple recommendations targeting the same component (`hero_banner`, `primary_cta`) combine and revert deterministically without hiding baseline sections (`hero_banner`, `primary_cta`, `service_cards`, `trust_summary`, `contact_intake`).
  - **Interactive Scope & Readiness Explorer:** Visitors can select project types, timelines, and readiness options to compute an immediate local scope summary with zero network side effects.

---

## 3. Security & Environment Boundaries

- **Production Gate:** All `/dev/visual-preview*` endpoints return `404 Not Found` when `MF_ENV=production`.
- **Staging Gate:** All `/dev/visual-preview*` endpoints return `404 Not Found` when `MF_ENV=staging` unless `MF_ENABLE_STAGING_VISUAL_DEMO=1` **and** the caller is an authenticated platform operator (`principal.user.is_platform_admin`).
- **CSP & Zero Inline Styles:** Composed HTML contains zero inline `style=` attributes and runs under `default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`.
- **No Unattested Storage or Third-Party Hotlinks:** Untrusted original bytes are never stored, and third-party image URLs are never hotlinked in `<img src="...">`.

---

## 4. Remaining Scope for Future Milestones (MF-017)

- Promoting `/dev/visual-preview` into the public anonymous `/assessment` / `/preview` onboarding flow and persisting attested media into permanent tenant storage upon account claim (`adapt_to_sitegen_profile` / `build_assessment_result_from_preview`) is reserved for **MF-017**.
