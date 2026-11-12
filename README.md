# MeasuredField — Unified Small-Business Platform (in progress)

MeasuredField is intended to help small-business owners build and run a business, not merely publish a website. It currently has three application surfaces: MeasuredField's discovery/public site, generated customer websites, and authenticated business/operations dashboards. This repository is an **unfinished staging candidate**, not a commercially launched product.

## Verified engineering capabilities (November 11, 2026)

- Multi-host FastAPI app with authentication, session/CSRF/RBAC, lead intake, owner dashboard, customer/project/proposal records, site versions, operator console and consent-gated shared learning.
- Versioned PostgreSQL schema (migrations 0001, 0002, 0003). Row-level security is enabled and forced on tenant tables. A restricted runtime database role is distinct from the migration owner.
- Operator overview, assessment detail, email outbox and learning-review pages all render with synthetic test data.
- HMAC-signed, path-bound notices; local redirect validation; production fails closed on untrusted forwarded-IP configuration.
- First-party response-time insights and deterministic Python growth-evaluation fallback when Node.js is unavailable. Search ranking, AI answer visibility, revenue and actual contact delivery remain unknown without connected evidence.
- Dedicated job worker, safe migration entrypoint, Render staging Blueprint and CI suite are in source.

## Local developer check (no database required)

From this folder, with Python 3.12 and dependencies installed:

    python -m pip install -r requirements-dev.txt
    python -B scripts/run_checks.py

This runs syntax checks and pytest. PostgreSQL-dependent tests are skipped unless a disposable PostgreSQL test server is explicitly supplied.

## Full database-backed check (requires Docker)

With Docker running and the cached postgres:16-alpine image:

    python -B scripts/run_disposable_pg_audit.py --full-suite

The runner creates an isolated PostgreSQL instance bound only to 127.0.0.1, provisions synthetic test databases and separate schema-owner/runtime roles, runs all unit and integration tests, and removes the container. Never point it at any customer database.

Narrower independent tenant-isolation audit:

    python -B scripts/run_disposable_pg_audit.py

At the last validated snapshot, the full PostgreSQL suite passed **101 tests with zero skips**. Rerun after changing the source; this is not live deployment certification.

## Staging deployment files (not deployed)

- render.yaml: Render web + worker, auto deploy disabled, providers default to none
- app/migrate.py: privileged MIGRATION_DATABASE_URL separate from restricted runtime DATABASE_URL
- app/worker.py: background queue processor and startup validation
- scripts/grant_runtime_role.sql: SQL role grant template for schema owner
- docs/DEPLOYMENT_READINESS.md: role bootstrap, proxy/DNS/TLS and release gates
- .env.example: placeholders only; do not commit actual secrets

No Render service, database account, domain, Stripe, Resend or Google OAuth connection is created by source files. All production actions require separate authorization and live testing.

## Areas intentionally incomplete

Custom domain DNS/TLS provisioning, hardened reverse-proxy attribution, live payments/email, object storage and document scanning, Google OAuth/Search Console, AI vendor budgets, backup/restore drills, and production tenant monitoring remain gated. See docs/CODING_AND_SECURITY_HANDOFF_20261111.md for detailed build/test status.

## Source provenance and data handling

The deterministic growth evaluator was derived from the existing PBC workbench and document chunker from Forge under its license. See workbench provenance records. Do not copy Prescott's Best Construction customer information or credentials into MeasuredField. The learning pipeline uses explicit consent and human approval; platform operators do not gain unrestricted tenant access.
