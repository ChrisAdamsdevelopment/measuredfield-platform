# MF-042 — AI Evidence Check: owner-readable provenance and confidence

**Status:** private development-only page, not public, not a customer performance guarantee, not a live provider integration. Published MeasuredField website, PBC production and paid AI calls are unchanged.

## Purpose

MeasuredField owners should not have to understand specialist search terminology to determine whether their business actually appears when AI answers a homeowner's question. The biggest integrity risk is treating imported screenshot percentages, generated test fixtures, historical question banks and live authenticated provider responses as equivalent.

The internal Prescott's Best Construction November 16 charts reported an apparent move from 27.3% (6/22) historical to 47.1% (32/68) recent. These are *different* cohorts, with unverified source receipts and unknown baseline dates. They are not a demonstrated one-day SEO improvement or confirmed cause-and-effect. The original chart values and caveats are preserved in D:\GrowthEngineeringVenture\03_CASE_STUDIES\PBC_AI_VISIBILITY_CHART_EVIDENCE_20261116.md and its machine-readable .json sibling. Those chart rows are NOT automatically inserted as trusted provider results.

## Product behavior

- A signed-in member in a local/test workspace opens AI Visibility Lab > Check which AI results are really verified, or /app/modules/visibility/evidence-health.
- The page explains how many saved records are first-party server-attested AI answers versus captured-but-unverified records, historical reference, synthetic software fixtures, reconstructed records, and unknown/incomplete records.
- When zero eligible verified yes/no answers exist, it shows 'No trustworthy recommendation percentage to show yet'—never 0% or a misrepresented collapse in visibility.
- A verified percentage uses only recorded yes/no observations with provenance=verified_provider, trusted server metadata flag and provider receipt ID, non-empty prompt/answer/model/provider and an observed timestamp. Records with unknown mention outcome are excluded from the ratio and counted separately.
- Imported origin fields cannot create server-verified observations: the existing import_evidence function forces verified_by_server=False and wraps untrusted client metadata. Unknown labels fail into review-required, never verified.
- An owner-readable note explains that AI answers, AI source references, search rankings, website clicks, phone calls and signed remodeling contracts are distinct metrics.
- Comparison caveat explicitly refuses to infer a one-day uplift from unequal cohorts unless timestamps, identical prompts, model, locations and test rules are verified.
- Zero writes, zero provider requests and no billing. The page is only present when non-production modules are enabled. The JSON companion is read-only and gated by the same signed-in tenant plus member permissions.

## Routes

GET /app/modules/visibility/evidence-health — accessible private overview. Existing workspace shell and RLS-backed TenantSession.

GET /api/v1/modules/visibility/evidence-health — private JSON summary of category counts and denominator logic, no raw provider text, account IDs or customer data.

Summary groups/tests live in app/services/visibility_owner_snapshot.py. Database work is one grouped SQL query scoped to current organization. No schema migration required.

## Testing

Unit tests cover empty data, mixed synthetic/historical/captured cohorts, true/false/unknown outcomes, unknown source types and malformed counts. Disposable PostgreSQL integration tests cover six evidence classifications, impostor verified_provider records missing trusted server metadata, two-tenant separation, denominator handling, browser access, unauthenticated API, read-only methods and RLS.

No live identity provider, secrets, paid calls, Search Console submissions or PBC production code is involved.

## Release constraints

This is an evidence *integrity* view, not a substitute for authentic provider receipt verification. Real provider ingestion must set trusted verification fields only after validating the provider response. Independent review remains required before production. Argon High was invoked in read-only mode for MF-041 on November 16 but returned HTTP 429 quota exhausted (reset reported around 116 hours); no Argon work or approval occurred.
