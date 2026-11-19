# MF-053 — Owner-first Growth Launch and AI provider choice

**Status:** Authenticated development-only preview; **not released**. This slice makes the disconnected feature collection into a simple, end-to-end decision path. No AI model execution, credential collection, customer data harvesting, Google API connection, paid SERP calls, publication or external account modification was enabled.

## Core customer journey

The owner answers plain-language questions rather than learning APIs or SEO jargon:

1. Business type, name, service area, whether the business has a site, and priorities (website, Google/AI discovery, customer inquiries, additional revenue, automated workflows).
2. Which accounts the **owner says** exist: Google Business Profile, Google Search Console, forms and CRM. A checked box is never considered a verified OAuth account link.
3. How they want assistance: free-tier Gemini starter when available, optional future own AI API key, ChatGPT interface, or no model at all.
4. An ordered plan built by deterministic source-labelled rules: website foundation → ownership of business listing → Search Console → on-page titles/alt text/canonical/schema → local ranking import → form-page attribution → lead rescue and follow-up → revenue offer validation → measure improvements → consented AI/account connections. Sections adjust to goals and whether the website is new.
5. Each action explains *why*, practical next move, what evidence or conversion to measure, prerequisites, approximate work effort, owner approval and a deep link to a **real existing workspace tool or labelled local preview**. Tasks are marked NOT STARTED; no false completed actions.

**Routes:** `GET /app/growth-start` and `POST /api/v1/growth-start/plan`; linked in owner workspace sidebar. Both are inaccessible to anonymous users, with authenticated member/tenant context. The POST uses ordinary anti-CSRF protection and refuses production access; the private preview uses no database writes. All fields are bounded/allowlisted, and unrecognized data—including accidental API keys—returns a generic 422 without reflecting the submitted value. An absolute 12 KB request-body limit applies. The browser renders all untrusted text with `textContent`; links are chosen only from a fixed list of owned internal routes.

## Provider routing and pricing, without a misleading all-inclusive promise

- **Gemini free starter**: Google publishes free-tier access to eligible models and restricted quotas. Quotas are generally project-scoped, **not multiplied by creating more API keys**. Free-tier submitted content may be used to improve Google products. Therefore, MeasuredField must not silently send leads, credentials, proprietary source code or client documents to a free-tier model. Paid Gemini tier or owner-confirmed disclosure policy needed for private customer data. Actual availability and capacity must be tested per connected tenant/project; the preview reports `not_verified`.
- **BYOK (bring your own key)**: Owner will be able to connect Gemini, OpenAI, Anthropic or another supported vendor with permissioned account-scoped, strongly encrypted credentials, no exposure to other tenants, explicit provider route, key rotations and easy Disconnect. **There is no credential input in this slice and no BYOK execution.** Next stage requires a reviewed tenant-isolated vault plus accepted provider terms and true credential validation. Storing keys in the browser or app source is forbidden. A customer's ChatGPT subscription is not an OpenAI API key.
- **ChatGPT connection**: The user can install the compatible MeasuredField plugin and use ChatGPT as a conversational control surface to owner-approved workspace tools. Full custom MCP and write tools currently require Business/Enterprise/Edu workspaces, while Pro custom MCP connections are limited to read/fetch per OpenAI Help Center (check latest plan policy before displaying checkout). A Pro subscription is NOT a universal prerequisite for the whole MeasuredField app. If a participating tool supports **Sign in with ChatGPT allowance sharing**, that has to be explicitly opted into; ordinary API calls are billed separately. The browser sign-in flow must be tested against a real HTTPS server and real IdP before claiming it works.
- **Manual / no AI**: Every business should still be able to create and follow a basic plan, import real evidence and capture inquiries without purchasing an additional AI subscription.
- **Google Search Console, GBP, Calendar, Gmail etc.**: Customer OAuth permissions are separate, minimized per scope and revocable. Search Console reporting/URL Inspection and site crawl/sitemap checks are NOT a promise of indexing. Google's **Indexing API is restricted to JobPosting and livestream BroadcastEvent URLs**: https://developers.google.com/search/apis/indexing-api/v3/using-api. Never use it as a universal indexing shortcut for ordinary services.
- **Google SERP/Maps geographic ranking data**: A separate provider tariff/owned license, not covered by free Gemini inference or a ChatGPT subscription. The newly completed MF-052 DataForSEO adapter is synthetic/offline and does not dispatch real paid queries. Server-paid vs customer-paid/vendor BYOK options should be explicit and consented.
- **Managed AI**: Future commercial tier where MeasuredField sells a defined allowance/cost ceiling and performs metered jobs. Do NOT use one shared personal Gemini free project as an unlimited production subsidy; project quotas, availability, privacy conditions and vendor terms apply.

Official references verified 2026-11-16:
https://ai.google.dev/gemini-api/docs/pricing
https://ai.google.dev/gemini-api/docs/rate-limits
https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus
https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform
https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt

## The operating system, not an AI wrapper

The durable advantage is **a governed, measurable business loop**, not just another prompt interface:

1. Owned business profile + website + approved third-party data become a bounded, source-labelled private workspace.
2. AI/provider model choice is a *plug-in to the execution layer*, not the source of truth. When no key or quota is available, deterministic checks and human-revised actions still function.
3. Site technical audit and content changes must be explicitly proposed, previewed, accessible/responsive, and tested before publishing. AI code is not presumed correct; use actual regressions, linting, schema validation and Search Console evidence.
4. Collect lead attribution from the real **page/form/call source**, consented UTM/referrer (without recording unwanted personal tracking), and appropriate customer inquiry linkage. Provenance matters: do not fabricate submissions/calls or claim attribution where unknown.
5. Tasks include evidence, owner review, implementation, measured before/after outcomes and rollback. Separate planned/approved/completed/verified results and don't confuse search impressions with revenue.
6. Revenue experiments are grounded in existing leads, repeat requests and capacity, not speculative guarantee of profit.

### Remaining implementation roadmap

**Next vertical slice priority**: tenant-isolated credential vault and UI under owner credentials, independently reviewed and encrypted/rotatable. Then a provider-routing adapter with per-task capabilities (vision, code, writing, web search, embedding), explicit privacy tier, allowlists, quota/error handling and spend limits. Never store plaintext API keys, never allow global fallback to someone else's key.

**Afterwards**: first-party Google Business Profile/Search Console/Analytics connections with real OAuth and source-specific permissions; verified website technical audits; a real funnel attribution record and page/form field mapping; consented lead follow-up drafts; a durable action queue shared across modules; fully integrated private owner dashboard; real HTTPS OAuth ChatGPT plugin connection + per-device gate; licensed geo-rank provider dispatch/receipts; conversion measurements. Each requires tested end-to-end owner workflow. Real customer data is not yet provided for proof.

## Tests and release controls

```powershell
python -B -m pytest -q tests/test_growth_journey.py -k "not authenticated"
node --check app/static/growth_journey.js
python -B -m ruff check app/services/growth_journey.py app/routers/growth_journey.py app/main.py tests/test_growth_journey.py
python -B scripts/run_disposable_pg_audit.py --full-suite
python -B scripts/secret_scan.py
```

No key collection or third-party action occurs in this story. The UI is a private local preview, not a production or live ChatGPT connection. Independent board review is required before release.


## Verified local implementation results — November 16, 2026, Arizona

- **24 focused Growth Launch rule/security tests passed**, one database-backed route test excluded from the focused run and included in the full suite. All options, goals and provider choices were checked for truthful staging and nonexecution. Invalid extra API-key data is sanitized; the server does not reflect submitted secrets in validation errors.
- Full disposable real PostgreSQL suite after the route and sidebar integration: **906 passed**, zero errors/failures/skips, `CI_GATE_PASS`, `FULL_POSTGRES_SUITE_EXIT=0`. The disposable PostgreSQL container was stopped and removed.
- Ruff for touched Python files passed; `node --check app/static/growth_journey.js` passed.
- Synthetic owner-only Jinja fixture rendered in headless Chrome desktop and in Chrome DevTools **true 390×844 mobile viewport**: `innerWidth=390`, `documentElement.scrollWidth=390`, single-column grid. Interactive form submission intercepted with a synthetic matching plan: results visible, 10 planned steps rendered, expected CSRF header present, **no API key submitted**, 0 model calls / 0 linked accounts. Screenshots in local AGENT_BOARD QA files; no real browser login or provider account was used.
- The final code is an authenticated private preview, not a publicly available release, stored credential vault, Gemini inference connector or verified ChatGPT connection. Remaining work is detailed above and requires security review.
