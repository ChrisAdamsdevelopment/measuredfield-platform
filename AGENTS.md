# MeasuredField / Antigravity coding agent rules

You are working on the MeasuredField multi-tenant small-business platform.

## Workspace and reference access
- Primary writable project: D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\mf_platform
- The launcher adds D:\ as a workspace to inspect existing code and project assets across the drive.
- Treat the rest of D: as reference/read-only by default. Do not modify Prescott's Best Construction production app, Forge originals, customer data, resumes, media or unrelated projects without a specific authorized task.
- Work in named copies or branches when migrating components. Preserve provenance, licensing and regression tests.
- Never disclose, copy into prompts, or package secret values from .env files, OAuth sessions, credential stores, database dumps or PBC customer files.
- Do not perform autonomous domain changes, deployments, payments, emails, purchases, deletes, mass renames or filesystem-wide refactors.

## Product boundaries
Three surfaces: MeasuredField marketing website; websites for customer businesses; private authenticated operations app. They share a secure data/control plane and never cross-tenant content.
- Owner-first discovery: What are you doing in your business right now that excites you? What's keeping you from taking it to the next level?
- Distinguish observed, inferred, synthetic and unknown business metrics.
- No public multi-tenant deployment until actual Postgres RLS, authentication and cross-tenant adversarial tests pass.
- Claude's mf_platform is still IN PROGRESS. Previously passed tests from earlier prototypes do not certify this application.
- Preserve source provenance for the PBC-derived growth engine and Forge-derived chunker; do not copy real PBC credentials or customer data.

## Bounded coding and verification
1. Inspect current files and git status before editing. Preserve other contributors' changes.
2. Implement a coherent, testable improvement, not a new unconnected prototype.
3. Run formatting, imports/compilation, focused unit tests and relevant regressions.
4. Diagnose failures, fix and retest. No more than three repair attempts on the same failure without reporting the blocker.
5. Record changed files, exact commands/results, risks and remaining blockers in a handoff note.
6. Use synthetic businesses and disposable databases; no live test email or payment.
7. Ask before irreversible operations; avoid dangerously skipping permission checks.
8. Keep the work reviewable by Claude, Gemini Argon and GPT-6.
