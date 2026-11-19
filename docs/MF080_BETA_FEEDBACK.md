# MF-080 — Feedback collection for the Founder Pilot

**2026-11-17 · Scoped implementation, not a claim of deployment.**

## Tester experience

An authenticated business user opens `/app/feedback` from the workspace sidebar, chooses Bug / Feature idea / General feedback, writes a 15–1,800 character description, and explicitly confirms delivery to MeasuredField's platform support team. The form tells testers **not** to include credentials, private customer records, financial information or API keys. No file uploads and no customer records are automatically attached.

The signed-in tester sees their recent feedback from the currently selected workspace and current review status. Each POST has the existing session, membership and HTML CSRF requirements and is capped to eight attempts per authenticated user per hour. The website footer links to `/login?next=/app/feedback`; anonymous users must sign in before sending.

## Operator experience

Only `is_platform_admin` accounts can access `/operator/feedback` from the existing operator console. They can read voluntary submissions and set new / reviewing / resolved; state transitions require operator-session CSRF. No emails, external AI calls or automatic ticket creation are performed.

**Privacy boundary:** Feedback is an intentional support disclosure, not tenant business content. Its platform-only `beta_feedback` table stores the submitter user id, workspace display name, exact submitted message, category, status and timestamps, without `org_id` and without pulling documents, inquiries, Gmail, websites or business data. It cannot be queried by other business tenants. Operators can see feedback submitted to the platform only, not general tenant tables. Message rendering is Jinja autoescaped. The database row is removed if the submitting user is deleted; future privacy/retention controls should define a shorter feedback retention policy before broad beta recruitment.

## Database and deployment

Add Alembic revision `0011_beta_feedback.py` on top of `0010`. Only the separate schema-owner migration role runs it; the web runtime user remains restricted and cannot bypass RLS. Updating code without running the migration will make feedback routes fail and may affect production startup checks. Keep the route behind authenticated accounts when launching publicly.

Provider settings remain `MF_PAYMENT_PROVIDER=none`, `MF_EMAIL_PROVIDER=none`, `MF_AI_PROVIDER=none`, `MF_ENABLE_REMOTE_MCP=false` for the first reviewed deployment, until their real integrations are separately verified.

## Acceptance

- SQL migration applies to fresh disposable PostgreSQL with two-role design
- CSRF and anonymous denials, operator-only queue and updates, rate limit, cross-business separation
- Output escaping and no leaked credentials
- Full PostgreSQL, Ruff and secret scans pass
- Independent review and controlled production release required

**Iteration loop:** Testers submit feedback; the team triages it via operator queue; a focused board story records a reproduction, code change, regression test, review, and versioned release. Do not turn submitted feedback into unreviewed training data or autonomous code changes.
