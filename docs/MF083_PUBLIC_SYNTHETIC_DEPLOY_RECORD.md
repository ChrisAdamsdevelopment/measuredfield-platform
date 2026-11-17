# MF-083 — MeasuredField public Founder Pilot deployment evidence

**Actual live deployment completed:** 2026-11-17 23:02 UTC (Arizona 16:02).
**Release commit:** `a595e7501875dea8d63445944a41ee3b5ea3ab01` (reviewed MF-082).
**Render service:** `srv-dbrk59jrjlhs73bha8pg`, existing SpectraCleanseAI workspace.
**Deploy ID:** `dep-dbudpdajnfac73cufkq0`; Render REST status `live`, Git branch `main`, autodeploy `no`.
**Last known working prior SHA:** `6c13a9928f805721be124e63c36813253ff9c959`.

## Existing public domains

- Canonical: `https://spectracleanse.com/` (MeasuredField-branded, HTTPS 200)
- `https://spectracleanse.app/` HTTPS 200
- Render host: `https://measuredfield-staging-web.onrender.com/` HTTPS 200
- Unchanged Prescott's Best Construction resources and credentials, no domain purchase or DNS edits
- Public pages use `https://spectracleanse.com` as canonical; no new MeasuredField-branded domain claimed

## Direct read-only live HTTP postflight

All checked against the canonical domain after Render declared the exact SHA live:

| Endpoint | Observed |
|---|---|
| `/healthz`, `/readyz`, `/` | 200 each |
| `/platform` | 200, contains 2023 Census CBP / 2024 NES, beta path and JSON-LD |
| `/assessment`, `/visibility-preview` | 200 each |
| `/sitemap.xml`, `/robots.txt` | 200 each, sitemap explicitly lists platform and preview |
| `/watch/measuredfield-business-launch` | 200 |
| `/static/media/measuredfield-hero-business-tools.webp` | 200 |
| `/static/media/measuredfield-business-launch-film.en.vtt` | 200 |
| `/login` | 200 |
| `/app/feedback`, `/operator/feedback`, `/app/market-intelligence` | 303 redirect to login (expected unauthenticated denial) |
| `/mcp`, `/.well-known/oauth-protected-resource/mcp` | 404 (MCP deliberately disabled until real Auth0 OAuth gates) |

## Live PostgreSQL and safety controls

- `dpg-dbrl0c3l550s73couo6g-a`, existing MeasuredField database
- Read-only postflight: Alembic `0011` and `beta_feedback` table exists; current PostgreSQL app role `NOT rolsuper AND NOT rolbypassrls = true`; `users` row count `0`.
- Prior pre-deploy revision `0004`; Render progressed `build_in_progress -> pre_deploy_in_progress -> update_in_progress -> live`.
- PostgreSQL plan remains `free`, expires **2026-12-13T18:00:16Z** without an upgrade. There is NO verified full production backup or restore, and free Render Postgres lacks managed recovery. This pilot is NONDURABLE; do not place real customer data or paying customer work on it. Owner explicitly stated they will personally upgrade before Dec 13 and instructed to deploy the public website and synthetic testing now without making a new paid upgrade.
- Public assessments can accept submitted information; until a managed backup exists, do not advertise this as durable production or invite real paying customers. If unsolicited real inquiries arrive, treat retention reliability as limited.
- Runtime config confirmed payment `none`, email `none`, AI provider `none`, remote MCP unset/off; autoDeploy `no`; Docker preDeploy command `python -m app.migrate`.
- The signed-in feedback workflow is deployed but no actual live user exists yet, so a real owner-to-feedback/triage journey has **not** been proven. Google/Gmail and ChatGPT real provider connections not claimed.
- Full disposable real PostgreSQL test gate: **1,084 PASS**, zero failures/errors/skips, source peer-reviewed.
- Existing Stripe production account was read-only inspected: Business Workspace and Growth Partner both $99/month; an existing 100%-off Founding Guided Launch coupon is once-only up to five uses. No charges or new coupons made.

## Next controlled tasks

1. Set up secure first operator login using the existing `python -m app.cli create-operator <email>` command via an authorized shell or restricted DB access, without printing or embedding passwords. Owner identity must be confirmed or derived from authenticated account ownership; never seed random public admins.
2. Provision two fictional businesses and measure actual signup/inquiry/reply/won-lost and feedback/triage in production, then remove synthetic records before any real customer.
3. Upgrade database as owner promised before 2026-12-13, verify real full backup and restore before any irreplaceable data or paid customers.
4. Validate Stripe sandbox subscription and intended founder-free access; don't switch `MF_PAYMENT_PROVIDER` to Stripe until webhook/entitlement gates pass.
5. Configure real Auth0 resource audience, separate OAuth clients, issuer/JWKS and `MF_MCP_*` env. Then turn on MCP with strict Host and Origin checks, test owner consent/revoke, update private plugin **MeasuredField** `plugins_6afcba53a45c8191b5c9511a991758aa` only after verified live endpoint.
6. Google Search Console request indexing for canonical `https://spectracleanse.com/platform`; sitemap route is live but indexing has not been requested or confirmed.
7. Keep source of truth GitHub `main`; no other worktrees. Current primary public release is pinned `a595e75`.

## Owner-approved release exception

Earlier MF-081 proposed a no-deploy gate without a verified full restore. The owner explicitly changed the rollout requirement to publish the full website now, use existing Free PostgreSQL in the interim, and upgrade before December 13. This release is therefore marked **synthetic-only, non-durable public pilot** rather than a backup-protected production launch. The absence of a full backup has NOT been disguised or marked passed.
