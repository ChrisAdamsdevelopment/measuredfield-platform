# MF-025 — Lead Rescue & Opportunity Intelligence

Date: 2026-11-15 (Arizona). Standalone third MeasuredField module, first working development-only slice.

## Purpose
Make existing unanswered inquiries and uncompleted follow-ups visible and actionable to small business owners. A real booked appointment or recovered opportunity is more valuable than an unsupported "potential revenue" number. Every item is linked to an actual tenant-owned Lead in the existing inquiry desk.

## Working implementation
- `/app/lead-rescue` is a private, authenticated, **read-only** workspace, disabled in production pending future entitlement and staging checks.
- The router queries only `Lead.org_id == principal.org.id`, with PostgreSQL FORCE RLS still enforced by the shared DB tenant session. No company can inspect another tenant's queue.
- Five mutually exclusive priority buckets, in order: overdue scheduled follow-up; due today; new with no workflow response timestamp after an internal 24-hour checkpoint; open with no follow-up date; scheduled later.
- SQL prioritizes urgent records, capped at 500 per request. It separately counts the total open set and explicitly labels partial coverage if more records exist. No silent 200-row limits.
- Each visible record shows only existing name, service, status, follow-up, internal response marker and an action link to `/app/inquiries/<id>`. No new persistent table, no outbound message, payment, AI call or client-side identity tracking.
- Business timezone uses `Organization.settings.timezone` if valid; otherwise UTC, explicitly labeled. All thresholds are repeatable in tests.
- First-response status remains a **workflow timestamp**, not independent proof that a text, call or email was actually sent. No estimated lifetime value, booking or conversion claim.
- Code: `app/services/lead_rescue.py`, `app/routers/lead_rescue.py`, `app/templates/app/lead_rescue.html`, `app/main.py`, `tests/test_lead_rescue.py`, this handoff.

## Independent standalone monetization concept
An owner pays for a daily "what needs attention" queue even without SEO, Google search rankings or model-connected automation. Differentiators to prove in pilot: reduction in missed inquiry follow-ups, elapsed time to first verified reply, number of assigned inquiries, and actual appointments booked after owner confirmation.

## Follow-up release work
- Make discoverable in sidebar/owner navigation under a scoped, separately reviewed story.
- Add optional contactability review and owner-approved outbound reply/delivery receipts; MF-004 is not complete. Never infer delivered from the lead.status field.
- Add verified booking and reminder integration only after per-owner authorized Google Calendar/email connector grant and consent.
- Add per-tenant usage quotas, opt-in notifications, historical comparisons, mobile browser testing, accessibility assessment and subscription entitlements before public release.
- Keep this product sellable alone, alongside (1) geographic Local Discovery/AI Visibility and (2) permissioned Computer Connect.

## Verification
- Focused deterministic classification tests, non-DB project suite and disposable PG tenant/session isolation tests are required.
- No production database, real business contacts, PBC records, paid APIs, DNS, Git pushes or deployments used.
