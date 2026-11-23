# MeasuredField Takeoff — existing-site authenticated pilot (2026-11-23)

## Owner-approved hosting decision
- Serve **/takeoff** from the EXISTING MeasuredField FastAPI application and existing Render service. Never replace its homepage, create an unrelated second service, repoint a customer domain, or place takeoff into public navigation yet.
- Use the same deterministic client-side takeoff engine as `D:\GrowthEngineeringVenture\17_MEASUREDFIELD_REMODEL_TAKEOFF_20261122\04_PROTOTYPE`. No hardcoded bathroom plans, quantities, prices, or model output in the release.
- Account identity in the first pilot comes from the **existing MeasuredField website session and active business membership**. This is the current email/password membership flow; it is NOT Google OAuth login for the website yet. The separate ChatGPT Live MCP OAuth Google connection continues to work independently.
- Enable only for explicitly listed business UUIDs. No server-side PDF uploads or takeoff database records yet. PDFs and projects are stored in browser IndexedDB, partitioned with a deterministic opaque SHA256 scope of current user+business. That prevents accidental cross-account project display in the UI on the same computer; it is not a substitute for encrypted cross-device cloud storage or a shared-device privacy policy.
- The existing MeasuredField business and API databases remain unchanged. No migrations.

## Source changes
- `app/routers/takeoff_pilot.py`: protected GET /takeoff + /takeoff/, returns current guided-app HTML, injects account-aware IndexedDB namespace and accurate signed-in user/business label, strict CSP, noindex and no-store. GET /api/capabilities reports remote AI **disabled** without leaking business data.
- `app/main.py`: only registers the new router. Existing customer-site HostRouter dispatch and main website unchanged.
- `app/static/takeoff/public` and `app/static/takeoff/src`: 52 allowlisted static assets, PDF.js runtime/license included. No original PDF test plans, NVIDA/OpenAI tokens, test figures or private .env.
- `scripts/stage_takeoff_site.py`: verified allowlist + SHA256 bundle copy from the independent source into canonical platform. Does not copy the independent local-only Python AI server.
- `tests/test_takeoff_pilot_route.py`: disabled/unauthenticated/foreign-business gates, authenticated real content and CSP, separate user+business IndexedDB namespacing, safe assets, no provider call.
- `scripts/takeoff_browser_smoke.py/.cjs`: test-only localhost owner identity, arbitrary **synthetic PDF** Chrome upload, scope detection, field measurement, material line, ChatGPT review packet, refresh persistence and phone width.

## Render environment switches for first site pilot
- `MF_TAKEOFF_PILOT_ENABLED=true`
- `MF_TAKEOFF_PILOT_ORG_IDS=<comma-separated approved business UUIDs>`

**Fail-closed:** If either is missing, invalid, or business is not in the list, route returns 404. A visitor who is not logged into MeasuredField gets redirected to /login?next=/takeoff. A signed-in user lacking the active business membership receives 403/404. The support/owner must invite Andrew to the correct business if he is not already a member; a ChatGPT OAuth account link alone does not create a website login session or invite.

DO NOT set `NVIDIA_API_KEY` just to serve this pilot. The current server has no authorized NVIDIA takeoff inference route, so that key would not be used. Implement a separate bounded image/text proxy with owner membership, CSRF, rate/cost limits, secure Render env injection, redacted logging and provenance review before turning remote AI on. The original **local** NVIDIA adapter remains available from the workstation but is not copied into the website release.

## Tests recorded on 2026-11-23
- `python -B -m pytest -q tests/test_takeoff_pilot_route.py`: **5 PASS**.
- `python -B -m pytest -q tests/test_takeoff_pilot_route.py tests/test_design_system.py tests/test_mcp_plugin.py`: **40 PASS**.
- `python -B scripts/run_checks.py`: **1147 PASS, 277 SKIPPED** due non-Postgres local test environment. GitHub CI full PostgreSQL/no-skip gate remains required before a Render deploy.
- `python -B scripts/takeoff_browser_smoke.py`: **PASS** on same-host FastAPI using simulated authenticated business. Real Chrome imported arbitrary synthetic plan, 11 extracted scope items, 1 material line from field input, downloaded owner-review JSON, loaded saved project after refresh, mobile 390 no overflow, zero JS/page failures. Early run caught static-path resolution bug; corrected by injecting document base before stylesheet/script. Last test showed zero console 404 after secure /api/capabilities route.
- `ruff check` pass.

## Important limitations / next milestones
- The page is not yet live on Render until source is reviewed/merged/deployed and the two environment switches are configured. No public navigation link added.
- Browser-local persistence survives refresh, not different browsers/devices. It is not cloud persistence; Andrew's projects will not appear on the owner's device unless he exports them. Avoid shared device usage.
- The ChatGPT web plugin currently verifies MeasuredField business membership and can read first-party business information. It cannot yet read IndexedDB from this app; Step 5 explicitly has the user download a review packet and attach it in ChatGPT.
- The first site pilot deliberately disables NVIDIA remote image interpretation; deterministic text/scope reading, measurements and price inputs continue functioning. No image request key is bundled.
- Before allowing customer data in cloud, implement secure business-scoped project snapshot storage with RLS, source/page IDs, owner consent and a read-only MCP takeoff tools bridge. AI corrections remain suggestions until the contractor explicitly accepts.

## Rollback
Remove `MF_TAKEOFF_PILOT_ENABLED` or set it to `false` in Render, and all pilot HTML/API routes disappear (404). No schema/database changes or existing production website alterations are required.
