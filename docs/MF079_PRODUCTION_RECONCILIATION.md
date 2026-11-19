# MF-079 — MeasuredField production branch reconciliation

**Local release candidate, NOT deployed.** Date: 2026-11-17 (Arizona).

## Source branches

- Current Render production: `feature/measuredfield-asset-production-20261112`, deployed commit `6c13a9928f805721be124e63c36813253ff9c959`.
- Two unique production commits that are **not** ancestors of development `main`:
  - `04cc1db`: MeasuredField brand assets, social metadata, responsive images, commercial offer art.
  - `6c13a99`: Gemini business-launch commercial, subtitles, video SEO, homepage sections.
- Reviewed development `main` merge input: `1c4bfbe` (MF-078 plus the approved OAuth MCP route and Census market tools).
- Isolated worktree: `D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\RELEASE_CANDIDATE_MCP_20261117`; local candidate branch `release/mcp-live-reconcile-20261117`. Main was not checked out, reset, rewritten or pushed.

## Merge resolution

A non-destructive `git merge-tree` preview identified **three** conflicts. Actual merge was performed only inside the isolated release worktree, with the current production branch as the first parent and reviewed main as the second.

1. `app/main.py`: retain production WebP/VTT MIME registration **and** modern OAuth route environment flag/lifespan/HostRouter protection.
2. `app/templates/public/base.html`: retain production logo, brand icons, social/manifest assets and commercial link; also retain new main navigation to Tools, Results and Pricing.
3. `app/templates/public/home.html`: retain production canonical URL, OpenGraph/Twitter previews, JSON-LD Organization/WebSite/Services, commercial video section, verified results evidence, responsive brand imagery; retain development's new product CSS, evidence-led tool descriptions and `mh-console`. The released hero media and two-question callout are kept in a separate responsive visual-overview section after the updated product hero rather than silently discarded. SEO/canonical/JSON-LD blocks render only when a real `settings.public_base_url` exists; synthetic Jinja contexts without a configured public URL do not emit a fake canonical or crash.

`tests/test_mf079_brand_mcp_merge.py` explicitly checks branded assets, video/caption availability, page metadata, product tools, MCP feature flag, MIME types, and no residual merge markers/duplicate Jinja blocks.

## Release verification and safety

- No actual production service or GitHub branch was modified. Render autoDeploy stays off and `/mcp` on the live older version remains 404.
- The MCP route remains **disabled by default** in the merged candidate and refuses unverified OAuth, issuer/resource, wrong Host and hostile Origin. Local test OAuth bears synthetic values only, never actual customer identity.
- The merged candidate must pass focused production brand/video and OAuth routing tests, **full disposable PostgreSQL suite with zero skips**, real MCP SDK 2.3.0 in-process conformance, Ruff and staged/tracked secret scans. Independent security and public-page design review is required.
- The resulting release SHA must be separately approved; do not force-push or repoint Render to this candidate without backups, migration/rollout checks, a rollback SHA, and a verified Auth0 resource audience.
- Even after deploying the candidate, the owner must configure `MF_MCP_*` and `MF_OIDC_LINK_*`, register distinct ChatGPT and first-party OIDC clients, complete real browser consent, and validate 401 protected-resource discovery, own-business read, cross-tenant refusal and account Disconnect. The current personal plugin `plugins_6afcba53a45c8191b5c9511a991758aa` is **not yet** connected.

## Quick commands

```powershell
git -C D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\RELEASE_CANDIDATE_MCP_20261117 status --short
python -B -m pytest -q tests/test_mf079_brand_mcp_merge.py
python -B scripts/run_disposable_pg_audit.py --full-suite
python -B scripts/mcp_private_connect_smoke.py --live --json
```

The last command probes existing live production and is expected to fail until the controlled MCP/OAuth deployment. Do not assume an offline source merge establishes a ChatGPT connection.
