# Staging gate and backup-recovery runbook

Status: the automated checks below exist and pass locally on synthetic data. **Nothing has
been deployed.** No Render service, database, domain or DNS record exists for MeasuredField.
Step-by-step Render setup is in `DEPLOY_RENDER.md`; this document is the gate that decides
whether you may run it, and the recovery procedure you must have rehearsed first.

## The gate: four automated checks, then a person

| # | Check | Command | Passing output |
|---|---|---|---|
| 1 | Full test suite on disposable PostgreSQL | `python -B scripts/run_disposable_pg_audit.py --full-suite` | `CI_GATE_PASS`, zero skipped, zero failed |
| 2 | No credentials in the repository | `python -B scripts/secret_scan.py --tracked` | `SECRET_SCAN_PASS` |
| 3 | Backup and restore drill | `python -B scripts/backup_drill.py` | `BACKUP_DRILL_PASS` |
| 4 | Staging configuration preflight | `python -B scripts/staging_preflight.py --env-file <file>` then, once the database exists, add `--database` | `STAGING_PREFLIGHT_PASS` |

Checks 1 to 3 need only Docker and the cached `postgres:16-alpine` image. They create their
own loopback-only databases with random passwords and remove them afterwards.

Check 4 reads configuration and prints the **name** of each problem variable, never its
value. Give it the staging values in a local file that is not committed (`.env.*` is
git-ignored and rejected by the secret scanner), or run it in the Render shell where the
variables are already set. `FAIL` lines block; `WARN` lines must be read and accepted by
the approver.

### Human approval (required, not automated)

`STAGING_PREFLIGHT_PASS` is not permission to deploy. Before the first staging deploy the
owner confirms, in writing on the agent board or in the project log:

- [ ] A new Render workspace is being used, separate from Prescott's Best Construction.
- [ ] The monthly cost of the web service, worker and database is known and accepted.
- [ ] The staging hostnames are chosen, and customer sites use a different registrable
      domain from the application (the preflight warns if they share one).
- [ ] Every `WARN` line from check 4 has been read. In particular: email off means owners
      are not notified of inquiries; proxy hops 0 means all visitors share one rate limit.
- [ ] Only fictional data will be entered on staging.
- [ ] Payments stay off, or use Stripe test-mode keys only (the preflight blocks live keys
      outside production).
- [ ] The backup section below has been read and the provider's backup plan is chosen.

No agent may deploy, create paid resources, change DNS or push to a remote repository
without that approval.

## What the backup drill proved (2026-11-12, synthetic data)

`scripts/backup_drill.py` built a database the way a deployment does (owner role runs
migrations, restricted role runs the app, forced row-level security), wrote two fictional
tenants through the restricted role, then:

| Step | Result |
|---|---|
| `pg_dump -Fc` as a role that bypasses RLS, `pg_restore` into a new database | both exit 0 |
| Row counts and content checksums, 32 tables, 121 rows | identical |
| Schema revision | `0004` in both; `alembic check` clean on the restored copy |
| Row-level security on the 17 tenant tables | enabled, forced, same policies |
| Restricted role against the restored copy | self-check passes; own tenant's rows readable; other tenant invisible; binary document intact |

### The trap this drill exists to catch

Under `FORCE ROW LEVEL SECURITY` the schema owner sees no tenant rows. The drill measured
what `pg_dump` does when run as that owner:

| How the owner ran pg_dump | Outcome |
|---|---|
| default flags | **refused** with a row-security error (good: it fails loudly) |
| `--enable-row-security` | **exit 0, and 0 of 50 inquiry rows in the dump** |

So a scheduled job that dumps as the migration role, with that flag added to "fix" the
error, produces a file that looks like a backup and contains no customer data. Rules:

1. Logical dumps must be taken by a role that bypasses RLS: the provider's admin user, or
   a dedicated `BYPASSRLS` role used for nothing else and never given to the application.
2. Never add `--enable-row-security` to a backup command.
3. Every backup job must check the row count of at least one tenant table against the live
   database before it reports success.

Render's managed backups are physical (whole-cluster) and are not affected by row-level
security, but that has **not been verified on Render** from this project.

## Recovery procedure to rehearse on staging

Do this once on staging before any real customer data, and record the date and duration.

1. In the Render dashboard, restore the database to a **new** instance (point-in-time
   recovery or the latest backup, depending on plan). Never restore over the live one.
2. Create a temporary copy of the web service pointing `DATABASE_URL` and
   `MIGRATION_DATABASE_URL` at the restored instance. The roles and their passwords are
   part of the cluster and come back with it.
3. From that service's shell run `python -B scripts/staging_preflight.py --database` and
   `python -m app.cli isolation-check`. Both must pass.
4. Sign in as a test owner and confirm a known inquiry and a known document are present.
5. Decide: repoint the real services at the restored instance, or copy specific rows back.
6. Delete the temporary service and, when no longer needed, the old instance.

Record: time of the simulated failure, the recovery point reached (how much data was lost),
and the time until step 4 passed. Those two numbers are the real recovery point and
recovery time; nothing in this repository can supply them.

## Rollback of a bad deploy

1. Render dashboard → the service → Deploys → Rollback to the previous deploy.
2. Migrations 0001 to 0004 are additive and each has a tested downgrade, so the previous
   code runs against the newer schema. Do not downgrade the database unless a migration
   itself is the fault.
3. Migration 0004 revoked all unused sign-in tokens on upgrade. That is not reversed by a
   downgrade; re-send pending invitations.

## What is still not verified

- Any of this on Render: role attributes of the managed database, physical backup and
  point-in-time restore, restore duration, proxy hop count, wildcard domains, TLS.
- Backup retention against the deletion promise in the privacy page (deleted workspaces
  remain in provider backups until they expire; the deletion receipt says so).
- Restore under load, or of a database larger than the synthetic 121 rows.
