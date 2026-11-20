# MeasuredField UI implementation reference — November 19, 2026

The owner supplied ten high-fidelity MeasuredField reference screenshots in ChatGPT.
These represent **design direction**, not proof that all pictured capabilities
are deployed or that shown KPIs, customer messages, receipts, or sales forecasts
are real data. Do not use example numbers in the customer's production UI.

## Source screens selected by owner
- Main dashboard: light canvas, white/lavender navigation, concise KPIs and
  clear first action.
- Connections & permissions: visible active grants, expiry, revoke, send/write
  scopes, per-account health and audit.
- Trusted Computers: per-computer trust, explicitly scoped folders, owner
  Work-mode session, durable work receipts and immediate stop.
- AI Visibility: actual source-labelled SEO/map/AI-answer evidence; compare
  by query/location/time; no invented rankings or projected percentages.
- Communications: connected inbox with truly callable direct send and precise
  provider status; never confuse a drafted/queued reply with delivery.
- Automations: practical owner-editable trigger/condition/action flow plus
  explicit run receipts and retry/error states.
- AI Website Workbench, Website Before & After, Design Center, Homepage Rewrite
  Studio, ChatGPT Change Requests, Approval & Publish Center: large website
  comparison high on page, movable purple split handle, desktop/mobile
  controls, ChatGPT collaboration, versioning, revisions and publish history.

## Implementation principles
1. Easy for nontechnical owners: navigation and outcome first, jargon second.
   Large controls, helpful empty states, keyboard/touch responsive layouts.
2. Real state: screenshots are conceptual only. Never invent counts, projected
   increases, enabled integrations, executed actions, or customer messages.
3. The before/after visualizer MUST compare the *same viewport alignment* of
   two actual website snapshots, allow slider input by touch/mouse/keyboard,
   and work on desktop and mobile.
4. Authorization is not impersonation: a linked identity, plugin installation,
   computer connection, local project grant and execution receipt are separate
   gates. Do not show 'Connected' if only an app has been registered in Auth0.
5. Actual work: build, edit, publish, communications and automations must call
   real services with audit and verified outcomes. ChatGPT without required
   MCP permissions cannot bypass owner limits.
6. The compare visual is now implemented for MeasuredField **hosted sites**,
   using existing SiteVersion and current Site.draft. Imported third-party
   CMS websites need a separate source ingestion and publishing adapter.
7. Product shell uses a cohesive light palette with violet action accents,
   green for verifiable success, warm warning and red for destructive actions.

## Work done this session
- New authenticated /app/website/studio customer screen, with live/saved
  snapshot comparison and working draft frame; no fabricated baseline.
- Shared sidebar/brand/topbar and responsive card-system refresh.
- Explicit human-readable current ChatGPT owner-link and computer-work status
  from persisted first-party records, plus recent real work receipts.
- Website/dashboard access to the new studio and keyboard accessible slider.
- Owner-approved task form now saves a **real** website work request in
  website_work_requests (migration 0019), with instruction, current draft
  SHA-256, status, audit event and owner cancellation.
- The ChatGPT OAuth MCP now has list_website_work_requests (mf:read) and
  apply_website_work_request (separate mf:website.write consent). The action
  changes the ACTUAL Site.draft, validates allowed fields (headline, intro,
  about, services, color), rejects a stale base, is idempotent and records
  the resulting SHA-256. It never publishes or sends messages.
- Those tools have source and real-PostgreSQL tests. An owner still needs to
  install/connect the actual ChatGPT MCP plugin and approve the permissions.
  The workbench is not an embedded ChatGPT chat service.
- Tests test_pg_website_studio.py on disposable PG18 plus existing editor and
  UI tests; local JS syntax and lint validated.

## Immediate next functional priorities
- Confirm production OAuth client enrollment and scope consent for
  mf:website.write, plus real linked owner, plugin install and end-to-end live
  website work verification. The Auth0 API scope alone is not an active tool.
- Extend the MeasuredField site generator to real layout blocks/imagery and
  responsive page variants. The current patch tool edits existing profile fields
  only; it does not yet arbitrary-rewrite CSS, create new pages or deploy code.
- Browser visual QA on responsive layouts with real website fixtures; expand
  420px mobile test and test reduced motion/keyboard comparison.
- Direct connected Gmail/Outlook authorization and outbox receipt, no draft-only
  promise. Signed desktop companion and scoped build/test/Git/deploy actions.
- Keep production rollout separate from pending protected-data recovery gates.
