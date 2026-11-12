# MeasuredField platform architecture

Status on 2026-11-11: runs locally against PostgreSQL 16 with the full test suite passing.
**Not deployed.** See `SECURITY_ASSESSMENT.md` for what blocks a public launch.

## One service, three surfaces

A single FastAPI process (`app/main.py`) serves everything and picks the surface from the
request's `Host` header:

| Request host | Surface | Code |
|---|---|---|
| listed in `MF_APP_HOSTS` | MeasuredField marketing site, the two-question assessment, service approval, sign-in | `app/routers/public.py`, `auth.py`, `templates/public/` |
| listed in `MF_APP_HOSTS`, path `/app`, `/api/v1` | Private owner/staff workspace | `app/routers/workspace.py`, `templates/app/` |
| listed in `MF_APP_HOSTS`, path `/operator` | MeasuredField's own operator console | `app/routers/operator.py`, `templates/operator/` |
| anything else | A customer's published website, looked up in `site_hosts` | `app/routers/site_host.py` |

`/healthz` (no database) and `/readyz` (one `SELECT 1`) answer on any hostname.

No domain is hardcoded. Moving to a new domain means changing `MF_APP_HOSTS`,
`MF_SITES_BASE_DOMAIN` and `MF_PUBLIC_BASE_URL`; no code changes.

```
                       Render web service (uvicorn, app.main:app)
 visitor ──https──►  HostRouter ──► app hosts ──► marketing / assessment / sign-in
                         │                   └──► /app workspace  ──┐
                         │                   └──► /operator console │   one transaction per
                         └──► other hosts ──► customer site + lead  │   request, bound to the
                                                                    ▼   signed-in organization
                                     PostgreSQL 16 (row-level security on every tenant table)
                                                                    ▲
                       Render worker (python -m app.worker) ────────┘  jobs: email, provisioning,
                                                                       lead notices, deletion
```

## Why customer sites share the platform instead of each getting a backend

Publishing a site stores an immutable rendered snapshot (`site_versions.html/css`). Serving
it is a hostname lookup plus one row read, so one small service hosts every customer site.
Rollback is repointing `sites.published_version_id` at an earlier snapshot. A lead's
destination is decided only by which hostname received it, so content and inquiries cannot
cross between businesses. Custom domains are rows in `site_hosts` that stay inert until an
operator verifies DNS/TLS.

## Data model

`app/models.py`, migrations in `migrations/versions/`.

**Platform tables** (MeasuredField's own records): `users`, `sessions`, `auth_tokens`,
`assessments`, `service_orders`, `payment_events`, `jobs`, `email_outbox`, `site_hosts`,
`rate_limits`, `lesson_reviews`, `shared_skills`, `shared_skill_versions`,
`deletion_receipts`.

**Tenant tables** (carry `org_id`, protected by row-level security): `organizations`,
`memberships`, `sites`, `site_versions`, `site_events`, `leads`, `lead_notes`, `customers`,
`projects`, `proposals`, `documents`, `memory_items`, `lesson_candidates`,
`assessment_reports`, `improvements`, `audit_events`, `data_requests`.

## Tenant isolation, in two layers

1. **Application.** The organization for a request comes from the server-side session and a
   membership row (`app/deps.py`). It is never read from a URL, header, query string or
   body. Roles (owner, admin, staff, read-only) are checked on the server for every
   state-changing route (`PERMISSIONS` in `app/security.py`).
2. **Database.** Every transaction sets two transaction-local settings, `app.org_id` and
   `app.user_id` (`app/db.py`). Migration `0002` adds a policy to every tenant table so a
   row is visible and writable only when its `org_id` equals `app.org_id`, with `FORCE` so
   it applies to every non-superuser role. With no context set, the application sees zero
   rows. An object id from another business therefore behaves exactly like an id that does
   not exist: 404.

The application connects as a restricted role that owns nothing and cannot bypass RLS;
migrations run as a separate owner role in the pre-deploy step (`app/migrate.py`). At
startup in staging/production the app refuses to run if its role could bypass RLS or any
tenant table lacks forced RLS (`isolation_self_check`).

Honest limit: RLS protects against a missed `WHERE` clause or an IDOR. It does not protect
against application code that deliberately selects another tenant's context. Three platform
code paths do set a tenant context without a member session, each narrowly: public-site
serving and lead capture (tenant from hostname), the worker (tenant from the job row), and
operator provisioning / domain verification / lesson decisions.

## The customer journey as built

1. `/` and `/assessment`: the two questions, then name/email/consent.
2. `services/assessment.py`: deterministic rules name what the owner values, organize the
   stated obstacles in the owner's own words, label each "you told us, not verified", state
   what evidence would verify it, map to capabilities and next steps, and recommend one
   offer. It says so when a problem is outside scope (e.g. financing) or unclear. It never
   fetches the owner's website and calls no AI model.
3. `/assessment/r/<token>`: result page; the owner picks an offer and types their name.
4. `services/orders.py`: approval is recorded as `approved_unpaid` (or `consult_requested`).
   With Stripe configured the owner is sent to Stripe Checkout and only a signature-verified
   webhook marks the order `paid`. Without Stripe an operator records an out-of-band payment
   with a reference, or provisions with a stated reason; the record then reads `NOT PAID`.
5. Provisioning creates the organization, copies the assessment in, turns in-scope obstacles
   into proposed improvements and invites the owner (single-use token).
6. `/app`: dashboard, inquiries, website (draft, preview, version, approve, publish,
   rollback, custom domain request), customers/projects/proposals, documents, improvements,
   insights, notes and playbook, team, activity, settings (export, deletion).
7. A visitor submits the form on the published site; it lands in that business's inquiries,
   a notice (without the inquiry's content) is queued for owners/admins, and the owner
   updates status, assigns, sets a follow-up, adds notes, converts to a customer.
8. Improvements show status and an evidence label: owner-stated, recorded by the platform,
   measured, or unknown. Publishing the site moves the website improvement to
   "implemented / recorded by the platform". Visit, form-start and inquiry counts are
   first-party daily totals with no cookies or visitor ids.

## Background work

`services/jobs.py` is a PostgreSQL queue: enqueue is idempotent by key and joins the
caller's transaction; workers claim with `FOR UPDATE SKIP LOCKED`; failures retry with
exponential backoff and end in `failed` after five attempts; a job stuck `running` for ten
minutes is reclaimed. `MF_INLINE_JOBS=true` runs jobs in the web process after commit (local
development and tests); production uses `python -m app.worker`.

## External providers (all off by default)

| Provider | Interface | State |
|---|---|---|
| Email (Resend) | `services/email.py` | Implemented, tested with a mock transport. With no provider, messages are stored as `not_sent_no_provider` and the UI says so. |
| Payments (Stripe Checkout) | `services/payments.py` | Implemented with direct REST calls and webhook signature verification; tested with mock transport and locally signed payloads. Never run against Stripe. |
| AI model | `MF_AI_PROVIDER` setting only | No model is called anywhere. Recommendations are rule-based and labelled that way. |
| Google Search Console | none | Not built. Shown as "not connected". |

## AI and learning

* **Assessment and insights** are deterministic rules. Insights reuse the PBC-derived growth
  evaluator (`app/vendor/growth_engine.mjs`) through a Node subprocess with a stripped
  environment, fed only first-party counters; if Node is absent a native Python review is
  used (`services/native_growth.py`).
* **Private memory**: notes and text-document chunks (Forge chunker) per tenant, keyword
  search only, never shared.
* **Shared playbook** (`services/learning.py`, adapted from the Shared Learning Hub):
  opt-in by an owner/admin, ownership attestation, automatic redaction and privacy checks
  that fail closed, an operator review queue holding sanitized text only, human approval
  creating a versioned skill, rollback, and automatic revocation on consent withdrawal or
  workspace deletion. Nothing is published automatically.

## Configuration

`app/config.py`. Everything is an environment variable; see `.env.example` and
`DEPLOY_RENDER.md`. Local setup: `python scripts/dev_setup.py --admin-url ... --reset --demo`.
