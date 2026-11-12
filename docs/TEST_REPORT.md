# Test report — 2026-11-11

All data is fictional. No real email, payment, DNS, deployment or customer record was
involved. These results show the code behaves as tested on this machine; they are not
evidence of production readiness (see `SECURITY_ASSESSMENT.md`).

## Environment

Windows 11, CPython 3.12.10, Node 22.22.3, PostgreSQL 16 (`postgres:16-alpine` in Docker on
127.0.0.1:55432). For each run the fixture in `tests/conftest.py` creates a new database,
migrates it as a schema-owner role, applies `scripts/grant_runtime_role.sql`, and runs the
application as a separate non-owner, `NOBYPASSRLS` role, the topology required in
production.

## Commands and results (final run)

```
cd mf_platform
set MF_TEST_PG_ADMIN_URL=postgresql://postgres:<local password>@127.0.0.1:55432/postgres
python -B scripts/run_checks.py        ->  102 passed, 0 failed, 0 skipped
(without MF_TEST_PG_ADMIN_URL)         ->   58 passed, 44 skipped (the PostgreSQL suites)
python -m ruff check app tests scripts --select E9,F   ->  1 unused import, no errors
python -m alembic upgrade head / check / downgrade 0002 / upgrade head   ->  clean, no drift
```

| File | Tests | Needs PostgreSQL | Author | Covers |
|---|---:|:---:|---|---|
| `test_pg_isolation.py` | 22 | yes | Claude | Two-business adversarial isolation: database, HTTP, public site, roles, CSRF, operator, uploads, XSS, export, deletion, shared learning |
| `test_pg_journey.py` | 5 | yes | Claude | The 15-step customer journey; versions, rollback, unpublish; proposal approval link |
| `test_pg_auth_payments.py` | 12 | yes | Claude | Sign-in, sessions, reset, invites, headers, Stripe checkout + webhook lifecycle, manual payment, email honesty, job queue |
| `test_pg_migrations.py` | 1 | yes | Claude | Upgrade/downgrade on a database that already holds tenant data |
| `test_pg_referential.py` | 4 | yes | collaborator | Foreign-key guarantees added in migration 0003 |
| `test_units.py` | 10 | no | Claude | Assessment engine, site generator, lead validation, client address, vendored-file hashes, chunker, redaction, growth bridge |
| `test_flash_security.py` | 13 | no | collaborator (GPT-6) | Signed notices, redirect safety |
| `test_lead_metrics.py` | 7 | no | collaborator | Response-time metrics |
| `test_operator_pages.py`, `test_operator_overview.py` | 11 | no | collaborator | Operator templates |
| `test_native_growth.py` | 5 | no | collaborator | Python fallback review |
| `test_deployment_preflight.py`, `test_health.py`, `test_proxy_rate_keys.py` | 12 | no | collaborator | Worker/migration preflight, health endpoints, header trust |
| **Total** | **102** | 44 | | |

Earlier prototypes, re-run after my one change to them (test import fix):

| Suite | Result |
|---|---|
| `node --test shared_core/test_reuse.mjs` | 5 passed |
| `python -m unittest discover -s shared_core -p test_reuse.py` | 4 passed |
| `python -m unittest discover -s public_site -p test_public.py` | 6 passed |
| Studio `python -m unittest discover -p "test_*.py"` (combined) | 8 passed (previously 2 errored when run together) |
| Studio `python qa_smoke.py` | 10 checks, PASS |
| Hub `python -m unittest discover -p "test_*.py"` | 21 passed |

## Failures found by testing, and what was done

| Found | Cause | Resolution |
|---|---|---|
| Baseline: 2 Studio tests errored when suites ran together | `import app` resolved to `public_site/app.py` once the Studio put it on `sys.path`. The earlier "54/54" figure came from running suites separately. | Test now loads its module by path. |
| Operator provisioning rejected by PostgreSQL ("violates row-level security policy for table audit_events") | The route cleared the tenant context before pending rows were flushed. | `TenantSession.set_context` flushes under the old context first (`app/db.py`). RLS caught a real bug on first contact. |
| A user in two organizations could see their own membership row from the other one | My first membership policy was too wide. | Policy narrowed in migration 0002: own memberships are visible only while no organization is active. Covered by `test_user_in_two_orgs_sees_only_the_active_one`. |
| A credential in a proposed lesson was not flagged | Vendored Hub filter redacts phones before API keys. | Raw-text secret check added in front (`learning.contains_secret`); defect documented, vendored file untouched. |
| Migration 0003 failed on a populated database | FK validation runs as the owner, which sees no rows under FORCE RLS. Passed on empty test databases. | FORCE lifted and restored inside the migration transaction; regression test added. |
| A developer `.env` changed a collaborator's test result | Settings auto-loaded `.env` from the working directory. | `MF_ENV_FILE=""` in `tests/conftest.py`. |
| Three of my own assertions were wrong (search box echo, str vs UUID, phrase present in the attacker's own draft) | Test mistakes. | Assertions tightened; no product change. |

## Manually checked

With the server running locally and a seeded fictional business
(`python scripts/dev_setup.py --admin-url ... --reset --demo`): the marketing page and the
published customer site at `canyon-view.sites.localhost` load in a real browser with
stylesheets applied, no console or Content-Security-Policy errors, and no horizontal
overflow at 375 px. Checked through the page's DOM and computed styles; **no screenshots
were captured and I did not click through the workspace in a browser.**

## Not tested (assumptions)

* Anything on Render: build, start command, pre-deploy migration, role attributes of the
  managed database, proxy headers, wildcard domains, TLS.
* Stripe and Resend against their real APIs (mock transports only). Stripe subscriptions
  beyond the first checkout.
* The worker process loop itself (`app/worker.py` is covered for preflight only; job
  handlers are tested through `run_due`).
* Browser behaviour of `site.js` and forms (no Playwright); screen readers; cross-browser.
* Load, concurrency beyond `SKIP LOCKED` semantics, large uploads under memory pressure.
* Backup restore; deletion effect on backups.
* The Docker image (`Dockerfile` was written, not built).
* The CI workflow has never run on GitHub.
* `test_growth_engine_bridge...` is skipped where Node.js is absent.
