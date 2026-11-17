# MF-081: Founder Pilot — approved code published, production deployment gated

**2026-11-17 (America/Phoenix). This is an actual release-status record.**
Owner approved Git consolidation, a public Founder Pilot, feedback collection, controlled Render deployment, and authorized local repo work. The owner has NOT yet agreed to a specific new recurring database fee, so no upgrade or charge was performed.

## Completed actual changes

- Local Git `main` was fast-forwarded to reviewed MF-079 merge `5f761b8`. Temporary worktree and local release branch were removed; the repo has ONE clean worktree.
- MF-080 beta feedback `64063dc` was reviewed and approved by Claude after **1,073** real disposable PostgreSQL tests (zero failures/errors/skips); `python -B scripts/backup_drill.py` restored 121 rows in 45 tables, preserving 25 tenant RLS tables under revision 0011. These are **local synthetic** tests, not Render backups.
- Reviewed GitHub `main` pushed fast-forward to `64063dcb21b723957c398891d755315b77e4f9f3`. Verified `origin/main` equals local main; NO force push.
- Existing Render service `srv-dbrk59jrjlhs73bha8pg` / `measuredfield-production-web` was changed to source `main` with `autoDeploy=no`. Existing public host `https://measuredfield-staging-web.onrender.com` stays on older live SHA `6c13a9928f805721be124e63c36813253ff9c959` until an explicit deploy.
- For the existing Render Docker service the pre-deploy command `python -m app.migrate` is **confirmed** at `serviceDetails.envSpecificDetails.preDeployCommand`, not at `serviceDetails.preDeployCommand`. It executes separately as the migrator, requires differing `DATABASE_URL` and `MIGRATION_DATABASE_URL` users/identical database names, then runs Alembic. No new deployment triggered.
- Existing public service provider switches read as `MF_PAYMENT_PROVIDER=none`, `MF_EMAIL_PROVIDER=none`, `MF_AI_PROVIDER=none`, `MF_ENABLE_REMOTE_MCP` unset (=off). These must remain disabled in the FIRST deployment until separately approved.
- MF-080 paths **after deployment**: `/login?next=/app/feedback` for invited testers; `/app/feedback` for authenticated submissions; `/operator/feedback` for platform-admin triage. They are NOT present in the currently running older deployment.

## Real database findings and release blocker

- The existing database `measuredfield-production-db`, Render `dpg-dbrl0c3l550s73couo6g-a`, is on **Free** compute (no Render managed logical backups or PITR), currently reports expires **2026-12-13T18:00:16Z** unless upgraded.
- Current live Alembic revision is `0004`, target code revision is `0011`. A new web deploy requires migrations to run successfully **before** serving new code. The old website remains available.
- Live users count is 0 and tenant organizations/leads read as 0 under a restricted role, but tenant RLS can hide tenant data, so that is **not a proof of empty database**. Approximate `pg_stat_user_tables` showed only migration version and rate-limit tables nonempty at inspection. No customer records were read.
- An attempted external logical `pg_dump` did not produce a verified full recoverable archive. The older RLS backup drill proves using the schema owner with `--enable-row-security` can silently OMIT tenant rows. Never call that a successful production backup. The model's safe tool execution blocked a later diagnostic command involving sensitive database credentials; do not circumvent that safeguard.
- Render's Free database is temporary; do not invite real customers, enable signup with irreplaceable data, accept payments, or migrate/create production customer records until a paid recovery-supported plan is approved and an actual backup+restore is verified.
- Render published pricing as of the date checked: paid PostgreSQL compute begins at **$6/month** for basic 256 MB plus storage charges (roughly **$0.30/GB/month** where applicable), subject to account checkout and plan availability. Upgrading recurring plan requires explicit owner approval for the price and any other costs. A paid plan with actual recoverability is required; check plan-specific PITR or backup features before choosing. Sources: https://render.com/pricing ; https://render.com/docs/postgresql-backups ; https://render.com/docs/free .

## Next production release transaction, once recovery is approved

1. Choose an explicitly priced paid Render Postgres plan; review incremental recurring charges and real recovery features. The owner approves it. Do not change the separate PBC Render services or share its Census API key.
2. Verify Render database is stable and capture a full backup through Render's managed mechanism or approved admin role. **Restore that backup to a separate database** and check row counts, schema, and RLS, including the 0-user edge case. Record evidence without tokens/customer content.
3. Verify migrator account still targets the exact same database as runtime, and has distinct privileges. Confirm application runtime remains restricted (not superuser/BYPASSRLS). Confirm required env names only and no shadow values copied from other customer businesses.
4. Trigger a manual deployment of exactly `64063dcb21b723957c398891d755315b77e4f9f3` from `main`. Keep autodeploy OFF. If the pre-deploy migration fails, stop; do not force startup or disable tenant safety. Prefer a time when a brief database deployment interruption is acceptable.
5. Postflight: verify actual Render deployed SHA/status, `/healthz` and `/readyz` 200, homepage branded media, `/login`, assessment path, app Host boundaries, and database Alembic 0011. Confirm `/mcp` remains off until separate real OAuth provider/client verified. Monitor error logs and health. Maintain last known working version `6c13a992` and a database recovery path.
6. Invite **one** controlled beta tester with synthetic business data, log in and send a genuine tester feedback message to `/app/feedback`, confirm it appears in `/operator/feedback`, change its status, and verify other business identity cannot read it. Capture actual HTTP results and screenshots only with explicit user account consent and no private data exports.
7. Run Stripe **sandbox only**, not live account, to test 100%-off promo, subscriptions, webhooks and trial expiry. Never charge without user-specific approval. For the first live pilot, a founder entitlement that bypasses payments is safer than fake charges.
8. Once the HTTPS `/mcp` OAuth protected-resource endpoint and scopes are configured, test owner consent/read/disconnect on the existing MeasuredField private ChatGPT plugin `plugins_6afcba53a45c8191b5c9511a991758aa`. A healthy marketing site does NOT imply the plugin is connected.

## Useful read-only checks

```powershell
python -B scripts/mf081_public_release_gate.py --live
python -B scripts/mcp_private_connect_smoke.py --live --json
python -B scripts/backup_drill.py
```

The release gate is intentionally fail-closed: even a fully checked metadata response **never grants automatic deployment or claims a completed ChatGPT OAuth session**. Without documented production restore verification, result is always blocked. The only deployment authority is an explicit operator-approved transaction after recovery is established.

**Live public website today:** https://measuredfield-staging-web.onrender.com (old revision until next deployment).
