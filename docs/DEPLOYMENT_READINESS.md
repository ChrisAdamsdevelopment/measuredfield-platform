# MeasuredField Render Deployment Readiness — staging blueprint

## Current status

The Render blueprint, pinned runtime requirements, dedicated worker, separate migration runner and SQL role-grant template are **prepared in source**. They have NOT been deployed to Render. As of November 11, 2026, disposable PostgreSQL 16 migrations through revision 0003, a restricted runtime role, representative tenant A/B RLS checks and the full 101-test integration suite PASSED. This verifies the local test environment, NOT Render deployment, live webhook delivery, domain TLS, production backups or a safe public launch.

## Separation of database roles (mandatory)

1. Provision a separate Render Postgres database for MeasuredField. DO NOT reuse Prescott's Best Construction's database or credentials.
2. The **migration user** must own the schema and run Alembic. Set this privileged URL only as MIGRATION_DATABASE_URL on the **web pre-deploy environment**, not in the worker or request handlers.
3. Create a **different, non-superuser, NOBYPASSRLS runtime role** (illustrative name mf_app). Provision its independently generated password outside source control and grant table access with scripts/grant_runtime_role.sql as the schema owner.
4. Set DATABASE_URL on web and worker to the restricted role's connection URL. The migration runner validates distinct usernames and matching database names, then runs migrations in a separate process.
5. Run the application RLS self-check and a disposable PostgreSQL two-tenant read/write/delete isolation suite before exposing staging to any real traffic. FORCE ROW LEVEL SECURITY is required. Do not infer successful RLS from local SQLite or template tests.
6. Render Blueprint environment variables marked sync:false are collected at creation time and might need manual updates for existing services. MF_SECRET_KEY is generated for the web service and forwarded to the worker through an environment reference. No actual secrets are included in render.yaml.

## Proxy / hosting constraints

- The web start command disables automatic trust of client-supplied X-Forwarded-* headers. This avoids header spoofing but app-level IP rate limits may group clients behind the Render proxy. Configure verified edge-IP attribution and edge rate limiting before public launch.
- HostRouter serves app hosts defined by MF_APP_HOSTS and customer hosts resolved through SiteHost. Set explicit HTTPS app hosts, public URL and sites-base-domain. Custom domain rows alone do not provision DNS or TLS.
- Do not enable MF_TRUST_PROXY_HEADERS. Do not use an unconstrained --forwarded-allow-ips=* setting.
- No custom domain or Render service has been created by this document. Render Blueprint auto-deploy is disabled; production deployment remains a separate authorization step.

## Services

- measuredfield-staging-web: FastAPI ASGI app, health check /healthz; pre-deploy runs app.migrate with MIGRATION_DATABASE_URL.
- measuredfield-staging-worker: app.worker polls PostgreSQL job queue, retries idempotent tasks and fails closed on unsafe staging configuration.
- Email, Stripe and AI providers start disabled. Even if configured later, actual provider workflows must be test-driven and reviewed.

## Preflight release gates

- All app routes/templates compile with synthetic tests, including operator pages.
- Locked Python dependencies install cleanly in a fresh Python 3.12 virtual environment.
- Real disposable PostgreSQL migrations, app-role isolation self-check, tenant A vs tenant B cross-access and owner/membership tests.
- Full synthetic assessment > payment webhook > organization > published site > lead > private owner desk > notification journey.
- Verified role permissions/CSRF/open redirects, signed notices, rate limits, file upload safety, email and payment idempotency.
- DNS/TLS/custom-domain design and rollback, backup and deletion restore drill.
- Owner approval for live domain, public launch, service charges, emails and real customer data ingestion.

See https://render.com/docs/blueprint-spec and https://render.com/docs/deploy-fastapi for currently supported Render commands and Blueprint fields.
