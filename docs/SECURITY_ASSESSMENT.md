# Security assessment — 2026-11-11

Written by the engineer who built most of this code, so treat it as a self-assessment, not
an independent audit. No penetration test or third-party review has happened. Everything
below was verified on a local PostgreSQL 16; nothing has run on Render.

## Verdict

**Suitable for a controlled staging deployment with fictional data. Not ready for real
customers or public traffic.** The release blockers are listed first.

## Release blockers (must be closed before real customer data)

| # | Blocker | Why it matters | What closes it |
|---|---|---|---|
| B1 | Never deployed. RLS, the two-role setup and startup checks are proven locally only. | Managed-database role attributes and proxy behaviour can differ. | Deploy to staging; run `python -m app.cli isolation-check` and the CI suite against a staging-like database. |
| B2 | Client address behind the proxy is unverified. With the default (`MF_TRUSTED_PROXY_HOPS=0`) all visitors share one rate-limit bucket. | Anyone can exhaust the sign-in or inquiry limit for everyone (denial of service), and limits do not slow a single attacker specifically. | Follow `DEPLOY_RENDER.md` §6; add edge rate limiting (Cloudflare) in front. |
| B3 | Password-only sign-in. No multi-factor, no email verification at sign-up beyond possession of the invite link, no breached-password check, no "sign out everywhere" screen. | Owner accounts hold customer contact data. | Adopt a managed identity provider (OIDC) or add TOTP. The session layer is isolated in `app/security.py` and `app/deps.py` to make that swap contained. Not built. |
| B4 | Payments are tested against mocks only, and there is no subscription lifecycle. The monthly offer would create a Stripe subscription that nothing tracks afterwards (renewal, failure, cancellation, refund). | Charging people without handling the lifecycle is a billing defect. | Stripe test-mode run end to end; handle `invoice.*` and `customer.subscription.*` events; until then sell only the one-time offer or invoice manually. |
| B5 | Email is tested against mocks only. With no provider, owners are **not notified** of new inquiries and invitations must be relayed by hand. No bounce or complaint handling. | The core promise (no missed inquiry) depends on the notice arriving. | Configure and verify a sender domain; test delivery; add bounce webhooks. |
| B6 | Operator actions on platform records are not audit-logged, and operators can read unsent outbox bodies, which contain live invite and reset links. | An operator (or anyone with operator credentials) could take over a customer account within the link's lifetime, with no trace. | Add a platform audit table; hide link-bearing bodies once a provider is on; require MFA for operators. |
| B7 | Outbox rows keep message bodies, including single-use links, in plain text until deleted. | Read access to the database equals account takeover within the token TTL (72 h invite, 45 min reset). | Blank the body after a successful send; shorten the invite TTL. |
| B8 | No backup restore drill. Deletion cannot remove data from provider backups (the receipt says so). | Recovery is unproven; retention claims must match reality. | Perform and document a restore; pick a plan with point-in-time recovery; state backup retention in the privacy policy. |
| B9 | Privacy and terms pages are honest drafts, not reviewed by a lawyer. No data-processing agreement for customers whose end-customers' data is hosted here. | Legal exposure, not a code issue. | Legal review. |
| B10 | No version control on the workbench. Several agents edited the same files during this build with no history; I detected concurrent changes only by file timestamps. | A bad edit to an authorization check could land unnoticed. | `git init` now; require the CI workflow to pass on every change; review diffs to `app/deps.py`, `app/db.py`, `app/security.py` and `migrations/`. |

## What is in place and tested

| Control | Implementation | Evidence |
|---|---|---|
| Tenant isolation at the database | Forced row-level security on 17 tables; restricted runtime role; fail-closed startup check | `tests/test_pg_isolation.py`: no-context reads return nothing; cross-tenant read/update/delete touch zero rows; inserting into, joining, or moving rows into another tenant is rejected by PostgreSQL |
| Tenant isolation over HTTP | Org from session + membership only | Owner of business A requests every object of business B across 6 read and 15 write routes: all 404, B unchanged; org ids in query, header, form and JSON body are ignored |
| Public lead routing | Destination derived from Host header | Forged `org_id`/`site_id` in the lead body lands in the receiving site's tenant; unknown and unverified hosts serve and store nothing |
| Roles | Server-side permission table | Read-only, staff, admin and owner boundaries; admin cannot mint, demote or remove owners; last owner cannot be removed |
| Sessions | Opaque 256-bit tokens stored as SHA-256; HttpOnly, SameSite=Lax, Secure in staging/production; absolute and idle expiry; revoked on logout and on password reset | `tests/test_pg_auth_payments.py` |
| Passwords | Argon2id (argon2-cffi), 12-character minimum, dummy-hash verification for unknown accounts | same |
| CSRF | Per-session token on every state-changing route, plus Origin check | Missing, wrong, another tenant's token, and cross-origin requests all 403 |
| Sign-in abuse | Per-account and per-client counters in PostgreSQL; generic error text | 9th attempt on one account returns 429 |
| Recovery and invitations | Single-use, expiring, hashed tokens; identical response for unknown addresses | reuse returns 410; unknown address creates no message |
| XSS | Jinja autoescape; site generator escapes every owner value; strict CSP with no inline script or style on all three surfaces | Script and attribute payloads in leads and site content render inert |
| Open redirect | `next` restricted to local paths; signed, path-bound notices | `tests/test_flash_security.py` (collaborator) and mine |
| Uploads | Extension allowlist, 5 MB cap, sanitized filename, served as attachment with `nosniff` and a sandbox CSP | executable, HTML, oversize, empty and path-traversal uploads |
| Payments integrity | Only a signature-verified webhook or an operator entry with a reference marks an order paid; events are idempotent; amount must match | unsigned, wrong-secret, stale, tampered, unpaid, wrong-amount and replayed events |
| Email honesty | `sent` only with a provider message id | no-provider, success, HTTP 500, missing id, network failure |
| Audit trail | Tenant audit events for meaningful actions; UPDATE blocked by a restrictive policy | tampering attempt changes zero rows |
| Export and deletion | Owner-only export; deletion with grace period, per-table receipt, honest statement about backups | deleting one business leaves another intact |
| Shared learning | Consent, attestation, redaction, fail-closed checks, human approval, revocation | no consent / no attestation / injected instructions / embedded credential all blocked; withdrawal revokes |
| Error handling | Users see a reference id; tracebacks go to the log; logs carry no bodies, query strings or addresses | 404/500 handlers |

## Known weaknesses that are not blockers for staging

* **Trusted application code.** RLS stops accidents, not malice in the codebase: three
  platform paths choose a tenant context without a member session (public site by hostname,
  worker by job row, operator provisioning/domain verification/lesson decisions). Keep those
  paths small and reviewed.
* **Customer sites share a parent domain policy decision.** Serve customer sites from a
  different registrable domain than the app (as large hosts do) before allowing any custom
  HTML or scripts. Today owners cannot inject markup, so the risk is low.
* **Spam.** Honeypot plus per-site rate limits only. No CAPTCHA or reputation service.
* **Documents** are stored in PostgreSQL (`bytea`) with no malware scanning. Fine at small
  scale; move to object storage with scanning before heavy use.
* **Proposal approval** records a typed name, date and revision. That is evidence of intent,
  not verified identity.
* **Redaction is heuristic.** The vendored privacy filter misses secrets that contain digit
  runs (compensated for, see `INDEPENDENT_FINDINGS.md`) and cannot guarantee anonymity. The
  human review step is the real control.
* **No dependency vulnerability scan** (pip-audit) has been run, and versions are pinned to
  what was installed locally.
* **No browser automation tests** and no assistive-technology audit. Pages use labels, focus
  styles, skip links and live regions, and were checked at phone width, by inspection only.
* **Insights** spawn a short Node process per page view of that screen.
* **Single website per organization**, single page per website.

## Privacy posture

* Prospect assessments (name, email, two answers) live in platform tables with no automatic
  expiry. Add a retention period.
* Published sites set no cookies and record only daily totals.
* Notification emails never contain inquiry content.
* The owner's website URL is stored as text and never fetched (no SSRF surface exists).
* No real customer data, PBC data or credentials are in this repository or its test data.
