# MeasuredField Gemini-Assisted Visual Website Improvement Previews (MF-015)

## 1. Architecture Overview

MF-015 builds directly on MF-014's SSRF-pinned static HTML scanner and deterministic widget preview engine to provide an evidence-linked, interactive **Before / After** visual website redesign experience:

1. **Brand Asset Discovery & Magic-Byte Sniffing (`app/services/brand_assets.py`)**:
   - `extract_asset_candidates(html_text, page_url)` extracts candidate logos, icons, OpenGraph images, `<picture>/<img srcset>` sources, JSON-LD `logo`/`image` properties, and CSS `background-image` URLs.
   - Relative URLs are resolved against the validated `page_url`. Any `data:`, `javascript:`, `file:`, credential-bearing, non-standard port, IP-literal, or internal hostname URL is rejected.
   - SVG files (`.svg`, `.svgz`, `image/svg+xml`) are automatically marked `verification_state="rejected_unsafe"` so embedded `<script>` or `onload` handlers inside SVGs can never reach the preview composer.
   - `sniff_asset_candidate()` reuses MF-014's `validate_and_resolve_url()`, `build_pinned_request()`, and `PinnedPublicIPTransport` to read at most 64 KiB in memory, verifying raster magic bytes (PNG, JPEG, GIF, WebP, ICO) and pixel dimensions without storing third-party media to disk or database.
   - All discovered media carries `license_state="unverified_candidate"` until an authenticated business owner attests ownership/usage rights.

2. **Typed AI Visual Schemas & Cross-Inventory Validation (`app/schemas/ai_visual.py`)**:
   - `BrandAssetCandidate`, `EvidenceItem`, `ProposalComponentConfig`, `ProposalChange`, and `VisualDesignProposal` enforce strict Pydantic v2 validation.
   - **Never Executable Code**: Any string containing `<tag>`, `javascript:`, `on*=`, `url(...)`, or `@import` is rejected at schema validation time.
   - **Zero Unverified Claims & Server-Controlled Disclosures**: Any string containing dollar amounts (`$`), labor-hour estimates, permit claims, license claims, search ranking guarantees (`#1 on Google`), conversion-lift percentages (`% more leads`), or false pre-approval/auto-publish claims (`fully approved`, `accurately verified`, `publish immediately`) is rejected. `VisualDesignProposal.ai_disclosure` is pinned to `DEFAULT_AI_DISCLOSURE`, `ProposalComponentConfig.requires_owner_confirmation` is locked to `Literal[True]`, and `MANDATORY_UNKNOWNS` are always preserved.
   - `validate_proposal_against_inventory(proposal, evidence_items, assets)` verifies that every `ProposalChange.evidence_ids` entry exists in the scanner's `EvidenceItem` list (and is not solely `unable_to_determine`), enforces the canonical deficiency-to-evidence contract (`DEFICIENCY_CHANGE_TYPE_TO_SOURCE_FIELD`, requiring `status == "not_observed"` for missing-feature change types and rejecting prose that contradicts `status == "observed"` evidence), and verifies that every referenced `asset_id` exists in the safe raster asset inventory.

3. **Server-Side Cost-Controlled Gemini Service (`app/services/gemini_preview.py`)**:
   - `GeminiPreviewService` is **disabled by default** (`enabled=False`, `monthly_budget_usd=0.0`).
   - Before assembling a prompt, `build_sanitized_gemini_prompt_payload()` screens all observed HTML titles, headings, CTA labels, and image `alt` texts through `detect_prompt_injection()` and instruction-override regexes, redacting any suspicious string to `"[REDACTED_UNTRUSTED_PAGE_TEXT]"`.
   - When enabled with a valid `api_key`, allowlisted model (`gemini-2.5-flash` or `gemini-2.5-pro`), positive remaining budget, and `user_disclosure_accepted=True`, the service calls the Gemini REST endpoint using the `x-goog-api-key` header (never query parameters) and scrubs the key from all logs and exceptions.
   - If the provider is disabled, unconfigured, over budget, times out, or returns unvalidated JSON, `propose(..., fallback_on_error=True)` deterministically falls back to `build_deterministic_visual_proposal()`.

4. **Interactive Before / After Preview Composer (`app/services/preview_composition.py`)**:
   - `compose_interactive_visual_preview()` merges the validated `VisualDesignProposal` with `PreviewConceptProfile` and renders an accessible studio containing:
     - **Toolbar**: Keyboard-accessible buttons to switch between `Split Compare (Before & After)`, `Before: Public HTML & Assets`, and `After: Interactive Redesign Concept`, plus `Desktop` vs. `Mobile (375px)` viewport framing.
     - **Before Pane**: MF-014's tri-state `DiagnosticObservationCard` plus the discovered `BrandAssetCandidate` inventory and owner rights attestation checkboxes.
     - **After Pane**: Interactive `ProposalChange` inspector (allowing the visitor to toggle individual recommendations on/off), safe candidate brand slots, MF-014's interactive Scope & Readiness Explorer widget, and the neutralized `#lead` concept form.
   - Emits zero inline `style="..."` attributes, zero external `<img src="https://...">` hotlinks, and zero network requests in `VISUAL_PREVIEW_JS`.

---

## 2. Shared-File Dependencies for Subsequent Joint Integration

Because MF-015 was implemented strictly within its reserved file scopes, the following shared files were left untouched and can be wired in the joint integration story:

1. **`app/config.py`**:
   - Add `gemini_api_key: str = ""` and `gemini_model: str = "gemini-2.5-flash"`, and document `"gemini"` alongside `"none"` in `ai_provider`.
   - Ensure `Settings.validate_for_production()` checks that `gemini_api_key` and a positive `ai_monthly_budget_usd` are present when `ai_provider == "gemini"`.
2. **`app/routers/public.py` & Preview Session Routes**:
   - Require explicit visitor checkbox disclosure before submitting a scanned URL summary to `GeminiPreviewService.propose(..., user_disclosure_accepted=True)`.
   - Enforce per-IP and global rate limits via `security.rate_limited()` before invoking scanner or AI preview services.
3. **Controlled Media Storage & Owner-Authorized Image Rendering Boundary**:
   - In this isolated milestone, the preview displays candidate asset metadata and structured slots (`data-mf-asset-slot`) without hotlinking third-party `<img src="https://...">` URLs, preventing referrer leakage, third-party tracking beacons, and unconsented copyright reuse while preserving strict `img-src 'self' data:` CSP.
   - To render actual business logos and photography in the upcoming customer-facing preview workflow, an authenticated owner must attest ownership/usage rights (`license_state="owner_attested"`). Upon attestation, the platform ingests the asset via `sniff_asset_candidate()` (pinned public IP, magic-byte raster verification, SVG/polyglot rejection, and size cap), strips metadata, and stores a clean raster derivative in MeasuredField's controlled static/object media storage served from `'self'`.
4. **Durable Application-Wide Monthly Spend Ledger & Quota Gate**:
   - `GeminiPreviewService.monthly_budget_usd` and `spent_usd` provide an in-memory per-instance circuit breaker for unit tests and bounded development runs, not a multi-worker durable global ledger.
   - Do not enable live Gemini calls publicly until a PostgreSQL-backed monthly AI usage ledger, per-tenant/per-IP quota accounting, and atomic budget reservation are wired into the shared database layer.
