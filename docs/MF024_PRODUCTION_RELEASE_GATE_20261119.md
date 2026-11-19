# MeasuredField — production release candidate & database safety gate

**Date:** 2026-11-19 AZ. **Release branch:** release/mf024-production-reconcile-20261119.
**Status:** source merge/testing only; NOT deployed, NOT billed, NOT customer-validated.
**Production Render web:** srv-dbrk59jrjlhs73bha8pg, deployed on main at
1c5d8db0ad0f4476737aeb82f74096aec4f324f6, auto deploy OFF.
**Canonical HTTPS:** https://spectracleanse.com/mcp.
**Database:** dpg-dbrl0c3l550s73couo6g-a, PostgreSQL 18, FREE,
expires at 2026-12-13T18:00:16Z per Render API.

## Source reconciliation and evidence

- Production-specific Git branch feature/measuredfield-asset-production-20261112
  has five local visual-preview commits absent from development main.
- Begin with that branch (7e96aea) and merge canonical main (dc2cfa6),
  retaining both the visual preview router and every new business/MCP router
  in app/main.py. Keep both pillow and mcp/PyJWT dependency pins.
- Only conflicts during non-destructive merge: app/main.py and requirements.txt.
  Do NOT reset, force push or replace a branch with the other branch's tree.
- Existing public brand icons, responsive video/website assets, before/after
  preview modules and new ChatGPT Work relay are preserved.
- Full candidate PostgreSQL 18? Tests use disposable PostgreSQL image, not the
  live PostgreSQL 18 instance. Full suite result: 1,347 passed, zero skipped,
  errors, failures; disposable PostgreSQL removed. Production compatibility
  still requires a real PostgreSQL 18 migration rehearsal.
- Focussed visual-preview, brand, Work and MCP tests passed 65; 2 tests
  intentionally skipped when PostgreSQL test fixture was unavailable;
  full database suite exercises them without skips.

## CRITICAL: free production database cannot be trusted for recovery

Render Free Postgres has **no backups** and **30-day expiry**. This particular
instance expires December 13, 2026. Render API shows one active database.
Read-only TLS metadata probe using Render's external connection info (never
printed/persisted) returned:
- PostgreSQL version 18, 46 public tables, 25 FORCE-RLS tables
- current database login has neither superuser nor BYPASSRLS privilege
- applied Alembic revision **0012**; candidate includes migrations through
  **0018**, i.e., 0013–0018 remain unapplied to production.

Do NOT treat pg_dump under this restricted role as a valid full backup:
FORCE ROW LEVEL SECURITY can omit business rows or abort a default dump.
Do NOT disable RLS in live production to bypass that; that creates a tenant
exposure window. Do NOT run any migration yet.

**Required paid-data safety gate:** upgrade to a paid Render PostgreSQL plan
with managed recovery (owner-approved billing); verify PITR is operational,
create a logical export via Render when available, and restore it into an
isolated database using a full-content data comparison. Alternatively,
develop and validate a complete secure per-tenant export + restore tool with
an explicit enumeration source and same-tenant RLS, and prove row-by-row
completeness before any release. No untested free-tier dump substitute.

## Release sequence after recovery is proven

1. Owner-authorized Render DB recovery plan and compatible Postgres 18 backup
   or paid plan. Capture recovery timestamp, restore test, database row counts,
   post-restore tenant A/B denial and RLS state.
2. Confirm Render production env variable NAMES, applicable env groups,
   correct public host, Auth0 issuer, Auth0 audience and exact ChatGPT callback.
   Never print credentials. Check MF_MCP_* and OIDC link credentials. The
   local Fernet master is in the protected file
   D:\GrowthEngineeringVenture\PRIVATE_AUTH\MeasuredField\
   MEASUREDFIELD_WORK_RELAY_20261119.env. Apply as MF_DEVICE_WORK_FERNET_KEY
   only when deploying a reviewed source and backing up the protected key.
3. Rehearse forward-only migrations 0013–0018 against a restored PostgreSQL 18
   snapshot, schema-owner vs runtime role separation and FORCE RLS validation.
   Beware that revision 0018 adds encrypted device_work_requests queue and
   composite org/device FK; fails closed if Work device access migration 0017
   has not been applied.
4. Push the reviewed release SHA to the configured Render deploy branch main.
   AutoDeploy must remain OFF. Disable remote MCP flag for initial deploy, and
   verify existing website/Stripe/webhooks, visual-preview feature controls,
   DNS, RLS and access boundaries before enabling MCP through reviewed deploy.
5. Set per-tool OAuth scopes and Auth0 connected app credentials. The Auth0
   third-party ChatGPT CIMD app currently grants 10/10 MeasuredField API scopes;
   DCR, CIMD and resource compatibility are ON. Real customer identity and
   consent are NOT proven merely by metadata.
6. Verify live no-bearer /mcp returns 401 with valid challenge, PRM metadata 200,
   DNS rebinding and Host/Origin restrictions, and a real Auth0 signed read-only
   owner account link. Then test mf:computer.write after owner consent.
7. Connect a real owner computer using the approved D: folder, start the
   signed/controlled desktop worker, queue a harmless synthetic file and
   inspect its actual on-disk bytes/hash and returned server receipt.
   Confirm own tenant, wrong tenant, expired session, revoked device,
   denied path, offline, duplicate job and consent behavior.
8. Update the **existing** MeasuredField private ChatGPT plugin (retain ID)
   with OAuth-protected streamable-http MCP configuration only after new tools
   are live. User completes ChatGPT plugin install/OAuth connection.
9. Later capabilities: bounded, separately granted safe commands, Git/deploy,
   and first-party customer-connected Gmail/Outlook direct send tools.
   These are NOT implemented by basic file Work mode alone.

## Tooling

Run read-only:
- python -B scripts/mcp_live_release_preflight.py --live --json
- python -B scripts/mcp_private_connect_smoke.py --live --json
- python -B D:\GrowthEngineeringVenture\Tools\MF_PG_Backup_Preflight_20261119.py

Source regression:
- python -B scripts/run_disposable_pg_audit.py --full-suite
- python -B scripts/secret_scan.py --staged
- git diff --cached --check

Do not publish any database export, secret, OAuth client secret or user files.
