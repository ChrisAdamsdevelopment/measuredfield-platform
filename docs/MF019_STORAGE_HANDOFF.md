# MF-019 — Tenant-isolated visibility research persistence

Date: 2026-11-15 (Arizona). Status: implementation in local development; independent review required.

## Scope and design
- Four PostgreSQL tables: visibility_gsc_snapshots, visibility_questions, visibility_experiments, visibility_observations.
- Migration `0005_visibility_storage.py` follows `0004`, introduces organization-bound composite foreign keys, tenant-filtered indexes, FORCE ROW LEVEL SECURITY with `app_org()` policy, and an update-deny policy on immutable evidence.
- New authenticated storage router `/api/v1/modules/visibility/storage`, included from `app/main.py`; read routes use member read permission; mutations require improvements.write and existing CSRF middleware.
- Explicit development-only guard: the router responds as unavailable in production. No Google Search Console OAuth, Gemini billing, model inference, remote desktop control, website publishing or user-visible subscription provisioning is activated here.
- GSC owner CSV rows are parsed and stored with SHA-256 digest, original CSV filename, explicit reporting dates only when provided, invalid row count and aggregate counts. Invalid or missing date ranges are not guessed.
- GSC-informed suggestions are saved as draft questions requiring explicit review. Saved plans validate original benchmark wording and approved custom-question identities. Plans preserve markets, methods, repetitions, workload counts, and 0 executed provider requests.
- Historical evidence is stored with server-classified provenance; synthetic samples and reconstructions are never promoted to verified provider observations. Original untrusted metadata is nested under `original_untrusted_metadata` and `verified_by_server` remains false.
- Imported evidence rows are append-only at SQL policy level, can be read within their tenant, and are included in tenant export/deletion order.

## Reserved implementation files
`app/models.py`, `app/main.py`, `app/routers/visibility_store.py`, `app/services/visibility_store.py`, `migrations/versions/0005_visibility_storage.py`, `tests/test_visibility_storage.py`, `tests/test_pg_visibility_storage.py`, `tests/_run_mf019_local_pg.py`, this handoff.

## Executable verification
- `python -B -m pytest -q tests/test_visibility_storage.py -p no:cacheprovider`: 12 passed.
- `python -B scripts/run_checks.py`: 203 passed, 91 PostgreSQL tests skipped by that non-database runner.
- `python -B tests/_run_mf019_local_pg.py`: previous focused disposable PostgreSQL run returned 4 passed.
- `python -B scripts/secret_scan.py`: PASS on 151 tracked Git files (before newly added source is committed).
- `python -B scripts/run_disposable_pg_audit.py --full-suite`: **CI_GATE_PASS — 294 real PostgreSQL-backed tests passed, zero skipped/errors/failures; disposable PostgreSQL container stopped and removed**.

## Dependency and operational boundaries
- MF-018 (Argon/Antigravity) owns `app/templates/app/modules.html`, `app/templates/app/visibility_lab.html`, `app/static/module_lab.js`, `app/static/module_lab.css`, UI tests and UI handoff. Do not include its uncommitted files in an MF-019 Git commit.
- Frontend currently uses the prior local analysis-only endpoints. An authenticated saved-state UI adapter, owner-facing import/approval controls and mobile browser checks are subsequent MF-018 tasks.
- Full production activation remains blocked on independent code review, approved staging, owner consent, retention/backup drill, real-provider verification and cost controls; do not label GSC organic impressions as AI mentions.
- Source records are not themselves independent provider-signed receipts; never claim causal uplift from these imports.
