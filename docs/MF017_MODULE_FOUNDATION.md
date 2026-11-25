# MF-017 Local Module Foundation — November 15, 2026

## Decision
Gemini Canvas is a prototype reference, not the long-term application. Develop independently useful modules in the ONE canonical MeasuredField FastAPI/Postgres source (mf_platform Git main). Collaborate with Argon High via Antigravity and Claude using the existing atomic agent board; do not create competing repositories or parallel long-lived branches.

## Local-only implementation
- Private, session/RBAC-gated /app/modules provides the first independent product modules collection.
- Private /app/modules/visibility provides the AI Visibility Lab research interface: import/paste local GSC CSV, show parsed rows/metrics, generate traceable draft homeowner questions, independently check/approve them, preserve original 22 benchmark questions, select any number, preview exact experiment calls across three prompt methods and a chosen market, export plan JSON.
- Local read-only API endpoints analyze a bounded CSV and propose questions, calculate complete request counts without silent truncation, and classify evidence provenance for review.
- 22 benchmark questions in one method plan 22 calls; all 3 methods plan 66; 22 questions across 2 markets plan 44; batching is only processing chunks.
- No Gemini requests, GSC OAuth or desktop actions are performed; no real data persists until exported. The module preview is disabled in staging/production. No subscription entitlements are asserted.
- Computer Connect is architecture DESIGN ONLY: explicit device pairing, restricted scoped grants, action approvals, append-only audit, revoke/stop. Default deny and no remote shell access.

## Source cohort safety
Uploaded exports reconcile to 182 distinct records: 22 historical imported (6 mentions); 22 quarantined reconstructions (11 mentions); 78 generated run-fresh78 examples (28 mentions); 60 timestamped run-live records (21 mentions; 18/57 non-branded, 3/3 branded). CSV labels alone are not independently authenticated provider receipts. Of the 60, 58 report grounded support and 2 queries only. The code-built 78 must NEVER enter live performance denominators. A 90-query GSC user snapshot totaling 349 query-row impressions, one click has unknown reporting window and is traditional Google organic data, not AI model rankings.
No claim that earlier site work causally improved AI visibility has been established; public owner approval and controlled before/after runs are mandatory.

## Modular platform priorities
AI Visibility Lab: tenant persistence, immutable provider receipts, authenticated/metered Gemini adapter, GSC OAuth, durable jobs and cross-city comparisons.
Computer Connect: least-privileged opt-in installed companion, mutually authenticated outbound connection, device permissions, per-action review, audit/revoke; separate major threat-model task.
Growth Intelligence: verified site audits and a measured implementation-to-outcome journal.
Customer Operations: improve existing lead, inquiry, project and follow-up features with authorized delivery evidence.
Website Studio: existing design previews and versioned, approval-gated publishing.
Further independent candidates: Review/Reputation Desk, Local Listings Consistency, Supplier Quote Intelligence, Content Proof Studio and Appointment Automation. $99-100/month per module is a hypothesis; actual value, provider usage and support costs must be tested.

## Product/release boundaries
No public teaser integration, subscription billing change, actual provider run, live GSC access, computer/device connection, publication or deployment in MF-017. Only internal local source and prototype; not production-ready.
Main checkout started at commit 705e513. Seven newer feature commits were preserved on their existing branch for later deliberate reconciliation; no reset or cherry-pick performed.
Future work needs tenant-specific Postgres migrations and FORCE RLS, OAuth/consent, quota and cost gates, provider receipt validation, idempotent worker queues, cross-tenant tests, browser/mobile QA and independent code review.

## Verification
Focused new module suite 16 passed. Existing entire no-database suite 191 passed and 87 PostgreSQL-only tests skipped. JS node --check and Python compile passed. Full database suite and UI browser testing are still required before release.

## Files
app/module_catalog.py, app/services/visibility_lab.py, app/routers/module_lab.py, app/templates/app/modules.html, app/templates/app/visibility_lab.html, app/static/module_lab.css, app/static/module_lab.js, main.py, base.html, tests/test_module_lab.py.
User-supplied full evidence CSVs are in the ChatGPT conversation; raw data was not automatically placed on D:. An evidence ZIP and audited report were generated as conversation artifacts for deliberate owner-controlled transfer to D:.

## 2026-11-19 Multi-Tenant Per-Business Configuration Remediation (Claude Review #460)
- Resolved the multi-business hardcoding defect identified in Claude's review of `380d87e`:
  - Added `TenantLabConfig`, `tenant_lab_config(org)`, `derive_brand_aliases(...)`, `build_tenant_benchmark_questions(...)` and `validate_tenant_lab_settings(...)` in `app/services/visibility_lab.py`.
  - The Prescott pilot set (`BENCHMARK_22`, `MARKETS`, `BRAND_ALIASES`) is seeded **only** when the signed-in organization is the pilot business (`_is_pilot_org`).
  - Every other tenant automatically derives brand aliases from its own business name (`Organization.name`), validates markets against its own configured service areas in `Organization.settings["visibility_lab"]` (or neutral service-area defaults), and receives a 22-question benchmark set scoped to its own business name and service areas—never Prescott's Best Construction.
  - Updated `import_gsc_csv`, `propose_questions`, `transform_question` and `plan_experiment` to accept `config: TenantLabConfig`, classifying each tenant's own brand queries as `"brand"`, mapping queries to the tenant's own service areas, protecting the tenant's brand aliases during controlled city substitution, and rejecting foreign markets outside the tenant's service areas.
  - Wired `GET /app/modules/visibility`, `GET/POST /api/v1/modules/visibility/config`, `POST /api/v1/modules/visibility/gsc/analyze`, and `POST /api/v1/modules/visibility/plan` in `app/routers/module_lab.py` to the authenticated tenant's configuration and tenant-scoped `VisibilityObservation` provenance counts.
  - Added unit and real PostgreSQL multi-tenant isolation tests in `tests/test_module_lab.py` with synthetic businesses in Flagstaff/Sedona and Tucson/Oro Valley.

## 2026-11-25 pilot identity review correction
Pilot question/market defaults now require the authenticated organization's immutable UUID to match server-owned `MF_VISIBILITY_LAB_PILOT_ORG_ID`. Missing/invalid configuration fails closed to ordinary tenant defaults. Business names and organization settings flags do not confer pilot status. Existing production preview gating is unchanged; no deployment configuration or live model entitlement changed. Configure the intended pilot UUID explicitly for development previews where appropriate. Unit and PostgreSQL route regressions cover a matching name in a different organization.
