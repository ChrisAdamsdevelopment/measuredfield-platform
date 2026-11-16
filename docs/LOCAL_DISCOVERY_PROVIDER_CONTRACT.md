# MF-021 Local Search and Competitor Intelligence Contract

Status: deterministic provider-neutral planning and evidence-validation foundation; NOT a live search API, website route, provider integration or paid search.

## Owner-friendly meaning

- Businesses found nearby: Places/provider business discovery; result order is NOT organic rank or map-pack rank.
- Organic search position: only a position explicitly returned by a trusted SERP provider with matching query parameters and timestamp.
- Local map-pack position: only a position explicitly reported by a trusted map-pack SERP source.
- Your Search Console performance: first-party Google organic impressions, clicks and average position. Not a competitor rank or AI-model citation.
- Needs owner review: possible competitor identity that cannot be verified automatically.

## Implemented files and constraints

- app/schemas/local_discovery.py: immutable Pydantic v2 schema for markets, coordinate anchors, keyword variants, provider capability profiles, request cells, observed source receipts and competitor identities.
- app/services/search_matrix.py: deterministic complete query matrix and provider request deduplication with explicit caps. No network, API calls, scraping or paid provider execution.
- tests/test_local_discovery_matrix.py: adversarial synthetic tests for localization, provenance, limits, and identity ambiguity.

A plan spans selected market x coordinate anchor x phrase variant x provider. Logical cells and distinct actual provider requests are counted separately; provider profiles that only support city text cannot claim distinct GPS results. The example of 3 anchors, 3 phrases and 2 profiles creates 18 logical cells, but only 15 distinct provider requests because some market-text requests share identical parameters.

Coordinates are sent only when the provider profile explicitly supports coordinate/radius localization; city-text and no-location profiles receive different request shapes. Requested device or locale applies only if the specific provider profile supports it; unsupported cells are counted rather than misrepresented. Each request has canonical immutable JSON parameters and a SHA-256 signature. Exceeding max_cells or max_unique_requests rejects the entire plan; nothing is truncated.

## Evidence and ranking discipline

SourceObservation requires timezone-aware observed_at, provider receipt ID, exact matching request signature, source family, evidence class and observed/not observed/unknown status. Positions are allowed only for verified-source organic SERP or local-pack SERP observations; Places business discovery cannot provide rank. GSC average position is a distinct owned-property metric, not a competitor or local-map rank. Synthetic/owner imported evidence cannot establish absent results.

Important: labeling an observation verified_provider_receipt in a schema does NOT authenticate it. A trusted server-side adapter must independently validate provider credentials, actual source response/receipt, allowed claims and the recorded time and parameters. Never allow client self-attestation to turn fake or synthetic rank into a measured statistic.

## Conservative identity resolution

Same provider namespace + equal place ID can automatically identify one physical location. If both records have no known place ID, matching verified domain + name + city may be merged. Same name/city alone is a review candidate. A shared company/franchise domain can represent multiple distinct locations; known different place IDs within the same provider cannot be merged even after alias review. Owner-reviewed aliases need named reviewer and evidence reference. Identity candidate cap 250, fail closed, no silent truncation. Parsing a website domain is not verification of ownership.

## Pending release gates

1. Owner-approved Places/SERP provider adapters, explicit terms/licensing and API metering/cost ceilings, no scraping.
2. Trusted receipts, immutable observation storage, tenant isolation, quotas/idempotency, timestamped source and coverage, repeatable comparisons.
3. Source-appropriate owner UI, multi-location comparisons without false GPS impersonation, owner-reviewed candidate graph.
4. Integrate MF-022 AI citation experiments as distinct observations (model/provider, question/market, real citations) without conflating Google organic/Places/AI visibility.
5. No live APIs, credentials, customer files, public website deployment or external spending are authorized by this task.

## Validation

python -B -m pytest -q tests/test_local_discovery_matrix.py
python -B scripts/run_disposable_pg_audit.py --full-suite

Use these familiar terms in customer-facing screens: Search area, Search phrases, Businesses found, Organic position, Local map-pack results, Your Search Console data, Last checked, Source, Not checked yet, Needs owner review. Keep internal SDK, provenance signature and API jargon in the engineering report.
