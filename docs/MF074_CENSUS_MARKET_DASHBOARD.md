# MF-074 — Source-led market-intelligence dashboard

**2026-11-17, local-only pilot.** The authenticated owner workspace now has /app/market-intelligence. It is NOT a published product, geographic ranking engine, named competitor directory or Google Business Profile connector. It reads only public U.S. Census data through independently approved MF-073. The 2023/2024 data was verified on official Census endpoints in MF-073. No AI, paid ranking source, owner personal information or customer data is sent to the Census provider.

## Owner journey

- A calm premium workspace page with one purpose: choose a geographic market and load an evidence-backed snapshot. The initial pilot intentionally supports Prescott Valley, Prescott, and Chino Valley, Arizona. This local-only seed is not a national market database or a global geography selector.
- GET requests require a real tenant membership and do not call Census. A CSRF-protected POST with an exact confirmation and allowlisted market slug makes six bounded public Census requests (a few may be cache hits). The site sends only permitted public geography/industry parameters; never the owner's name, website, email, account number or tenant data.
- Four source-labelled cards show town population and households (2024 ACS five-year), construction employer establishments (2023 CBP, county) and construction nonemployer establishments (2024 NES, county) separately.
- Further rows show CBP business ZIP establishments, county residential-remodeler establishments NAICS2017 236118, ACS ZIP Code Tabulation Area population/households, county payroll, nonemployer receipts, all with units and source years.
- A retrieval-time ledger links to key-free official Census APIs. Missing or suppressed data is visibly unavailable, never quietly zero. Partial upstream outages preserve available cards and identify missing categories.
- Market descriptions make the key distinction explicit: a business ZIP (CBP), a ZIP Code Tabulation Area (ACS), a Census place and a county do NOT cover identical boundaries. Figures do not denote named businesses, currently licensed contractors or present-day SEO ranking.
- Empty/error states, fully labelled form, read-only confirmation, focus states, 390px mobile-friendly responsive cards, table with inner horizontal scrolling, reduced-motion support. No simulated map pins or fabricated rankings.

## Security, scope and backend

- GET /app/market-intelligence requires membership via existing tenant-scoped session.
- POST /app/market-intelligence/run requires member_post read permission: valid CSRF token, real tenant membership and read permission. A request with invalid slug, missing exact confirmation or unapproved session is rejected before the adapter runs.
- Per-user 15 and per-workspace 40 snapshots per hour with existing PostgreSQL atomic rate_limited service. This wraps an adapter which independently caps requests and caches public aggregates for up to one hour. Public statistics may be cached between tenants; NOTHING private is cached.
- No arbitrary user-controlled URL, NAICS, ZIP, FIPS, Census endpoint or key is used in these routes. Only preapproved regional slugs map to literal FIPS/place and ZIP identifiers.
- Failures do not echo upstream credential values or request URLs. No keys passed into template context.
- Service adapter remains in app/services/census_market.py (MF-073; not changed in this story). The canonical deployment variable is `MF_CENSUS_API_KEY`; the owner is renaming the Render variable to match. Live Render runtime configuration has not been verified. Never log or move the actual key.
- Route registered in app/main.py; workspace nav link added in app/templates/app/base.html. No existing owner accounts, demo rankings or other modules are modified.

## Test boundaries

- tests/test_pg_census_market.py exercises authenticated GET, anonymous denial, POST CSRF and confirmation, data from fake in-memory public Census provider, geography allowlist/injection rejection, suppressions/partial failures, rate limits, cross-tenant page rendering and customer website Host separation on disposable real PostgreSQL.
- Synthetic page and mobile browser QA run on NO real identity data, a Chrome DevTools emulated 390px mobile viewport. Record inner viewport width/document scroll width and inspect page screenshots.
- Full disposable PostgreSQL tests, lint, tracked+staged secret scan and source diff check required before submitting to a different reviewer.
- Independent reviewer must confirm key omission, CSRF and quota semantics, true geography labels, accessible actions, no cross-tenant leakage, and no false live-competitor assertions. Do not approve simply because tests pass.

## Follow-ons

- MF-075 read-only, owner/tenant scoped ChatGPT Census tools; ChatGPT is not automatically installed by completing MF-074.
- Confirm Render has the exact `MF_CENSUS_API_KEY` variable name after the owner updates it; no alternate key names are supported or needed. Validate via safe runtime readiness (never printing the credential) before a production rollout.
- Generalize geography beyond Prescott-area slugs only with reliable address/place/ZIP disambiguation, correct boundaries and bounded requests.
- Claude's separate draft for a Free Business Checkup proposes PageSpeed, CrUX, RDAP and Arizona ROC official sources AFTER quota/terms/license research. That draft is unapproved and not part of this story. GBP/GSC remain separate consent/eligibility workflows.
