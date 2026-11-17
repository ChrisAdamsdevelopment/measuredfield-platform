# MF-056 — Unified MeasuredField program architecture, release readiness and construction takeoff roadmap

**Date:** 2026-11-17 Arizona  · **Status:** owner-approved product direction; document-only implementation  · **Canonical repo:** `D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\mf_platform`

## Decision and guardrails

MeasuredField is **one coherent, premium owner-facing growth and operations platform**, not a bundle of disconnected software projects. The central product loop is **observe evidence → diagnose → suggest a bounded improvement → owner approves → execute with a rollback path → independently measure the outcome**. Its business dashboard is accessible, understandable to a nontechnical contractor, and responsive on desktop/mobile. Reusable tenant-scoped modules may provide trade-specific capabilities, beginning with a human-assisted **construction drawing takeoff and estimating workbench**.

The construction engine is a modular experiment, **not a pivot away from** Google Business Profile, Search Console/GA4, geographic ranking evidence, lead attribution, website approval/rollback, ChatGPT in-app connectivity, and an exceptionally polished UI. Prefer a small tested module and integration contracts to a monolithic autonomous architecture.

No app source changes or live integrations are authorized by this roadmap alone. No customer plans, paid SERP/model calls, credentials, OAuth registrations, public deployments, external submissions, publishing or sending bids without separate explicit authorization. Retain single-main-source Git, local-only workboard, tenant FORCE RLS, owner consent, budget reservations, audit and peer review.

## Readiness: what is real and what is not

Read **current** docs rather than interpreting passed tests as production readiness:

| Capability | Existing work | Verified status / release gate |
|---|---|---|
| Tenant foundations | MF-001/002/003/005/007/009/010/011/012 and more | Board DONE, local tested; production deployment and privacy/security verification still separate |
| Modular owner dashboard | MF-017/MF-018, MF-042, MF-053 | Local preview or code review; MF-017/018 and MF-053 await independent review |
| ChatGPT read MCP/OAuth | MF-037, MF-039/040/041, MF-044/045, MF-050/051 | OAuth HTTP/SDK tests and user account-link UI are **local only**; MF-051 preflight found **14 missing/malformed configuration names and 3 warnings**, no real OAuth IdP, no live ChatGPT plug-in installation; MF-037/039/040/041/045/051 in REVIEW; MF-050 depends on gates |
| Local computer | MF-027..034 and MF-043/046/047/048 | Private local host-only companion and protocol regression; newer enforcement/SDK repairs require peer review; never assume arbitrary ChatGPT access to user disk |
| Geo evidence | MF-021/022, MF-049, MF-052, MF-036 | Offline fixtures, import or provider response contract; **no verified live geo grid or AI visibility paid request**, MF-036/049/052 await peer review |
| Leads / attribution | MF-025/026, MF-055 | Lead Rescue base done, full provenance/lead outcome story MF-055 ready but blocked on MF-053 review |
| AI provider BYOK | MF-054 | Ready but depends on MF-053 and credentials security gates; **no owner vault or production provider key route yet** |
| Google GSC/GA4/GBP | MF-059/060 planned | No demonstrated customer OAuth/API reads; GSC/GA4 can request least-privilege account/property permission, GBP requires independent Google Business Profile API approval; no mock data passed off as current |
| Takeoff | Argon R1/R2 offline artifacts; MF-063..071 planned | **No professional real-plan benchmark or functional editable takeoff app** |

Latest MF-053 record reports 906 full disposable PostgreSQL tests passing with no skips on **2026-11-16**; that is a local repo regression checkpoint, **not** a proof of live Google/ChatGPT/staging, commercial use or publication. Before entering production, rerun exact integration and cross-tenant tests against approved staging with fictional tenants, independent review, backup/recovery and security/consent verification.

## ChatGPT connection path — test privately, then publish

1. **Review unblock:** independent reviewers validate MF-037/039/040/041/045/046/047/048/051 and complete any changes. MF-050 has specific prerequisite reviews. Do not self-approve authored work or deploy unreviewed code.
2. **Offline protocol:** preserve official MCP 2.3.0 read-only tool schemas; test resource 401 challenge, protected-resource discovery, JWT scope/issuer/audience/expiry, tenant RLS, Host=200 / foreign Host=421 / hostile Origin=403, OIDC PKCE/CSRF/state and disconnect revocation. Confirm no unauthorized stdio bypass.
3. **Preflight:** `python -B scripts/chatgpt_connection_preflight.py --json` (safe issue codes only). Current audit recorded 14 missing/invalid configuration items + 3 warnings; zero real account link. Security checks must not echo secret values.
4. **Approval for external staging:** choose domain/HTTPS host, isolated PostgreSQL, trusted OAuth/OIDC authorization provider, correctly scoped redirects/JWKS, controlled hosting budget and synthetic tenants. This requires an owner decision; do not select production credentials implicitly.
5. **Private in-ChatGPT installation:** perform the official custom-MCP/Plugin installation and interactive authorization in a supported workspace; demonstrate actual read-only tools for the synthetic tenant, rejection of another tenant, and immediate refusal after Disconnect. Capture objective timestamps and minimal non-sensitive evidence.
6. **Public package and review:** verify contemporary official developer terms and plugin package, metadata, brand assets, privacy/support pages, OAuth requirements, domain ownership, positive/negative test prompts, accessibility, installation and uninstall. Submission acceptance and public directory appearance are independent external approvals. Do **not** claim 'published' from passing tests.
7. **Later write/desktop path:** only separate permission-scoped approval queue for website edits, bid changes or local computer files with preview, rollback and audit. No blanket D-drive, desktop shell, public site publishing, paid calls or email.

**Clarification:** A trustworthy usable core and real private end-to-end testing precede public submission. No need to complete every future module before starting controlled private testing. ChatGPT subscriptions and OpenAI API charges are separate; plan/entitlement statements require checking official current documentation before sales copy.

## Premium interface and owner journey

Develop design primitives **before** scattered feature polishing: consistent typography, color contrast, whitespace, iconography, restrained motion, keyboard focus, touch targets, empty/loading/error/offline/synthetic states, readable numeric precision, localization of error messages. The home surface is a small number of useful outcomes (inquiries, next action, local visibility, account connection health) rather than a wall of SEO metrics.

A typical 60-year-old owner can tell what is connected, why a task is suggested, its cost, whether it is merely a preview, what owner approval permits, and whether a real result occurred. Responsive website owner workflows are mobile-first; a construction measurement canvas may rightly be optimized for mouse/large-screen accuracy, with field review accessible on mobile.

**MF-058** design system follows independently reviewed MF-053; avoid premature shared base-template edits until its dependency is marked DONE.

## Google and geographic verification

- MF-059 GSC/GA4: least-privilege OAuth property chooser, user-authorized site, token vault and revocation; do not infer per-lead search keyword from GSC aggregate query data.
- MF-060 GBP: Google requires a separately eligible/approved project for Business Profile API and user's verified managed location; `business.manage` permits broader operations, so owner-level permission and *read-first* behavior are mandatory.
- MF-061 geo: after MF-052 and MF-036 independent review, provider-specific paid-API dispatch with explicit reservation/caps, real task IDs, precise coordinates/keywords, dated result source, rank-surface separation, no-results/partial failure, and owner-unverified import labels. A heatmap from fixture or Places relevance must not be sold as actual geo rank evidence.

## Construction takeoff — engineering decision from Argon R1/R2 and independent rerun

**Supporting material:** uploaded `r1.html`, `r2.html`, `takeoff_vertical_slice_prototype_goeswith_r2.py`, `prescott_remodel_verified_takeoff_estimate_goeswith_r2.xlsx`; independent review in `D:\GrowthEngineeringVenture\00_PROJECT_MEMORY\research_notes\MF_TAKEOFF_ARGON_R1_R2_INDEPENDENT_AUDIT_20261117.md`.

The 810-line synthetic Python prototype generated its *own* vector architectural PDF and its own degraded raster, then used `pdfplumber`, `pypdfium2`, Shapely and OpenCV to extract geometry. An independent rerun of that supplied script found 6 closed polygon outputs at 0% **against its synthetic fixture**, 12 counted artificial symbols (with four decoys), approximate raster living-area 259.8 vs 270 sq ft (~3.78% difference), a known 5.4 PDF point / 3.6 in wall boundary gap flagged for review, and 16 fixed sample estimate lines totaling $8,161.53 direct and $11,936.24 quoted at example overhead, contingency and margin. There is **no** blinded real-plan proof; the room naming and scale calibration depend on predefined page coordinates, unknown polygons can self-define their own ground truth, and the symbol precision test does not independently match detected locations to ground truth.

The workbook has some useful area/material/package/price formulas, but estimated labor hour cells **P5:P20 are constants**, so quantity edits do not recalculate labor correctly. Input prices, labor rates and 'APPROVED/CONTRACTOR VERIFIED' states are example values, not sourced and signed professional quotations. Do not double-count isolated interior holes/islands as finished floor surface or silently add gap-bridged geometry as verified. R1/R2 raster percentages used different fixtures or versions, so keep each test's provenance rather than averaging claims.

### First vertical slice / hard gates

**MF-063** document legal plans, per-sheet/viewport scale policy, ground truth review and error methodology. **MF-064** implement real PDF ingest and user-edited calibrated polygons/holes/lines in the main repo with deterministic geometry. **MF-065** link reviewable material assemblies, waste, units, package rounding, dated contractor/supplier prices, labor production and cost/markup/margin formulas, audit-true CSV/XLSX. **MF-066** provide professional, aesthetically cohesive PDF canvas for zoom/pan, exact polygon correction, source-to-estimate row selection, versioned approval. **MF-067** run 25 independently measured, authorized and blinded project sets including raster/vector/multi-scale drawings, report per-trade accuracy, exception and time-to-completion; provisional vector <=0.25%, verified snapped raster <=1.5%, 50% time reduction targets are hypotheses until evaluated. **MF-068** after benchmark investigate review-only CV proposals and build-vs-licensed Kreo options. **MF-071** scoped authenticated ChatGPT read tools/approval-queued proposals after module permissions and connector reviews. Never publish a bid or place a material order from an unreviewed AI suggestion.

Technology candidates (legal/license review before adoption): pypdfium2/pdfplumber, Shapely, pdf.js drawing overlay, FastAPI service boundary, PostgreSQL tenant RLS, OpenCV/ONNX for later proposals. Do not create another permanent worktree, branch or platform; isolate module by packages and interface in canonical repo.

## AI Module Studio and industry templates

**MF-057:** versioned, permissioned curated installable module manifests, enable/disable/remove per tenant and no unrestricted disk/network/credentials. **MF-069:** AI proposes *declarative* schemas/forms/formulas/workflows with synthetic preview, constraints, owner approval, version and rollback—not arbitrary generated scripts executing in production. **MF-070:** cleaning quote/schedule and restaurant ingredient/yield/margin templates after safe framework. **MF-072:** research optional Android/tablet camera/offline/notification field companion after desktop takeoff UI; phone native permission does not grant unrestricted user computer access or bypass Google/ChatGPT OAuth.

## Explicit board sequencing (per currently seeded dependencies)

- **MF-056** (this decision record) is first and requires peer review before DONE.
- Foundation: **MF-057** depends MF-017 + MF-056; **MF-058** depends MF-053 + MF-056. Google **MF-059/MF-060** depend MF-054 + MF-056; **MF-061** depends MF-052 + MF-036 + MF-056; **MF-062** depends MF-050 + MF-051 + MF-045 + MF-041 + MF-056.
- Construction: **MF-063 → MF-064 → MF-065 → MF-066 → MF-067 → MF-068**; MF-066 also requires MF-058, MF-067 requires MF-065 and 066. **MF-071** requires MF-065 + MF-037 + MF-057.
- AI Studio: **MF-069** requires MF-057 + MF-058; **MF-070** requires MF-069. Android **MF-072** requires MF-058 and MF-066.
- Existing board story statuses are mutable; inspect real `board.py status` and exact story before claiming or editing. Files allocated per story. Do not bypass unmet review dependencies to accelerate a release.

## Minimum acceptance for the program decision

The owner can see which *product outcome* each module serves, which data/action permissions it needs, which stage is genuinely verified, and which item must finish first. All accuracy, ROI and delivery claims have explicit real evidence; unknowns remain unknown. A future release gate independently checks security, accessibility, privacy, payment, provider access, backups/rollback, account linking, operational response and approved customer data retention, not merely nominal 906-suite success.

**This MF-056 changes documentation only.** Owner consent to undertake a local story does not authorize platform production submission or external API account connections. Independent board reviewer decides DONE.
