# MF-020 — Saved AI Visibility Research Workspace

Prepared November 15, 2026 (Arizona). Canonical source: mf_platform Git main. This is development-only, not live search or production.

## Delivered UI (six MF-020 files only)

- `app/templates/app/visibility_lab.html` adds a private saved-workspace panel on the existing AI Visibility Lab page. It offers owner CSV import, optional explicitly provided reporting window, explicit saved-question approval, named plan saving, and browsing of saved sources/questions/plans.
- `app/static/module_lab.js` exposes minimal in-page state via a synchronous custom event; no localStorage, remote calls, or device access. Tracks the exact analyzed CSV, rejects stale CSV edits for saving, and marks built-in synthetic examples and unverified imported draft files as ineligible to be saved as genuine CSV.
- `app/static/module_persist.js` calls MF-019's authenticated same-origin tenant-bound APIs only: `/snapshots`, `/snapshots/{id}/suggestions`, `/questions`, `/questions/{id}/review`, and `/experiments`. All writes include the existing session CSRF header; read-only users receive view-only controls; server enforces roles and CSRF independently. Source data is only uploaded when the owner explicitly clicks Save. Imported sample never silently becomes owner data.
- `tests/test_visibility_persist_ui.py` verifies Jinja role signal and controls, runs a real Node.js mocked DOM and fake same-origin API through the actual bridge, and validates owner/read-only behavior against real disposable PostgreSQL.
- `app/routers/module_lab.py` remains unchanged; the adapter consumes existing MF-017 preview routes and MF-019 persistence routes without confusing local analysis with executed provider searches.
- Saved plans are **plans**, not scheduled work: provider_requests_executed = 0. Historical GSC CSV impressions are traditional Google organic traffic and must not be presented as AI citations or model ranks.

## Honest limits / next engineering gates

1. Saved records can be reopened across authenticated devices, but restoring an editable full draft in the form is not yet implemented: saved source rows/plan configuration can be inspected, and export exists. An authenticated edit/resume workflow requires a later ticket.
2. Model execution, provider metering, retry queues, verified response receipts and spending approvals are not enabled. There are zero live provider calls here.
3. GSC OAuth has not been wired. Owner-supplied CSV date range is unknown unless entered. CSV export alone does not independently prove Search Console ownership.
4. No public teaser, live coordinates search, Google/Maps ranking or production website release in MF-020.
5. Passwords, API keys, local device files, PBC data and credentials are never copied into code, tests, logs or public demos.
6. Independent review and full disposable PostgreSQL gate are required before any staging or entitlement change.

## Next product tracks requested by owner

### MF-021 — Local Search & Competitor Intelligence (new standalone paid module)

- **Query matrix:** search phrase variants × city/market × explicit coordinate anchor × radius/viewport × provider × requested device/locale when the provider actually supports those parameters; reproducible runs with timestamps, query IDs and caps.
- **Provider sources must not be conflated:** (a) owner-authorized Search Console API for owned organic search, (b) Places Text Search for nearby business discovery by coordinate/radius (not a guarantee of exact personalized organic rank or map-pack position), (c) approved SERP data provider for actual organic/local result positions subject to its contract, (d) optional first-party Google Business Profile and Maps business info when authorized.
- **Competitor identity graph:** normalize by provider place ID, verified website/domain, business name and city; keep aliases, categories, evidence URLs and merge decisions distinct; never infer a company is a competitor solely from a name match.
- **Evidence:** record exact provider, field permissions, observed timestamp, request parameters, location resolution, source URL/receipt reference, search-result position only if explicitly returned, and sample coverage. Never fabricate missing prices, service territories, ranks or lead counts.
- **Comparisons:** presence/absence by city and source; controlled quote variants; leading competitors and business profiles; trend comparisons only between comparable, repeatable methods with sufficient observations; source and uncertainty always visible.
- **Cost/abuse:** tenant budgets, query quotas, request de-duplication, terms-compliant caching and provider licensing. No arbitrary scraping or stealth proxying.

### MF-022 — AI Discovery & Citation Intelligence (separately valuable)

- Ask approved questions against selected supported providers/models using verified server-side calls, provider response metadata and sampling plan. Report brand mention rate, citations and link references, competitor mentions, run-to-run variance, models/version, prompt wording, time and source provenance.
- Coordinates are NOT automatically supported for AI-model web search. The OpenAI web-search API supports approximate city/region/country/timezone location, while a Places provider supports coordinate/radius biasing. Make differences transparent; do not claim identical GPS impersonation or universal 'AI rank'.
- Keep historic, reconstructed and synthetic records quarantined from live denominators. Provide owner-facing evidence inspector and reproducible exports.

### MF-023 — Public Preview / Free Teaser + Website Before/After

- Reuse existing MF-014/015/016 SSRF-hardened, read-only diagnostic and interactive visual demo. Offer a small, explicitly bounded diagnostic of a public business site, a location-scoped sample lookup when provider and credits are available, then an owner-controlled subscription/claim path.
- No owner login, no remote desktop/file connection, no publishing, no bulk scraping and no claim of verified uplift. Clear "sample / indicative / unknown" labels, rate limits and spend caps, captcha/abuse controls when needed.
- Contrast preview findings with the private paid multi-location comparison, historical snapshots, owner data, approved optimizations and meaningful follow-through.

### MF-024 — Secure ChatGPT / Computer Connect

- Existing MF-012 gateway is **read-only tenant-bound**; MF-013 OAuth and approved write flows remain blocked. Customer needs to explicitly pair each computer, grant a **narrow folder scope and type of action**, approve reads and consequential changes, inspect access logs, revoke access and disconnect.
- ChatGPT custom MCP integration uses a supported remote MCP endpoint or secure tunnel plus independently authorized device-local companion. The service must not automatically browse the customer's drive or silently grant arbitrary shell access. Reauthenticate for consequential actions, never embed secrets in prompts and require user action for provisioning.

### MF-025 — Lead Rescue & Revenue Opportunities (third independently paid module)

- Build on existing persisted Lead, LeadNote, LeadEvent, message and follow-up models rather than a duplicate CRM. Deterministic overdue follow-up and first-response aging, contactability and evidence-backed missed-opportunity queue by status, date and owner, with explicit counts/coverage.
- Identify site-to-inquiry conversion friction only from actual site traffic/form events and timestamped lead records, not guesses. Optional reply drafts must need owner approval; "sent", "delivered" and "responded" require distinct verified provider states.
- Business value can be measured in previously missed follow-ups addressed and actual booked appointments, not invented revenue attribution. A standalone app is useful even before advanced AI or browser/desktop connectors.

## Recommended integration sequence

Finish and review MF-020; then implement MF-021 provider-neutral immutable search comparison contract with synthetic fixtures; integrate one approved provider under per-tenant quotas; add AI discovery instrumented model runs (MF-022); publish a one-query, strongly rate-limited teaser after stable provider costs (MF-023); complete customer-consented OAuth/MCP/file scope (MF-024); expand lead rescue into its own product module in parallel (MF-025).

### Provider documentation reference
- Google Places Text Search: https://developers.google.com/maps/documentation/places/web-service/text-search
- Google Search Analytics: https://developers.google.com/webmaster-tools/v1/searchanalytics/query
- OpenAI Responses web search: https://developers.openai.com/api/docs/guides/tools-web-search
- ChatGPT custom MCP connecting: https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt

## MF-020 corrective review — cross-snapshot provenance (November 15, 2026)

Independent Gemini Argon review identified a genuine bug: the saved-workspace UI previously allowed an older approved question with identical text but a different GSC source snapshot to satisfy a new source's draft or planned experiment. The original module was returned from review rather than approved without correction.

Corrective behavior:
- Explicit question approval matches the active saved snapshot ID as well as question text and GSC origin, never substituting an older same-text approval.
- Saving an experiment with any GSC-derived question requires the currently analyzed genuine saved CSV. Synthetic and unsaved CSVs cannot reuse previous approvals.
- Experiments may use GSC question IDs only from approved questions attached to the active snapshot; benchmark-only plans remain available without GSC imports.
- If the active source question is missing from the API response (which is capped at 250 recent workspace questions), the UI fails closed instead of binding a different snapshot. A source-filtered API is needed for complete pagination.
- The Node mocked client test includes an older approved same-text question before the current draft and verifies that the current question alone is reviewed and chosen in the plan, and rejects unsaved/synthetic and missing-current-source paths.
- No external model execution, Google API call, OAuth, spending, customer access or publishing is enabled.

Next backend improvement: source-filtered paginated GET /questions?snapshot_id=... or stable question IDs returned by source-specific suggestions. This must preserve tenant authentication, FORCE RLS and real ownership checks.
