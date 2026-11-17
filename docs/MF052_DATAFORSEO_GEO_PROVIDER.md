# MF-052 — DataForSEO geographic SERP request and evidence contract

**Status: Implemented, synthetic-only source boundary. NO REAL RANKING CALLS, VERIFIED RECEIPTS, VENDOR CREDENTIALS, LIVE BILLING, ONLINE DEPLOYMENT OR OWNERS AUTHORIZED.** This is intentionally not a working Google geo-grid scan yet. The implementation is ready for independent review before adding authenticated dispatch or persisted observations.

## Why this exact vendor and contract

DataForSEO documented four distinct, geo-targetable search surfaces (source updated September 18, 2026):

| Product search surface | Vendor endpoint | Result item type | What rank means |
| --- | --- | --- | --- |
| Google organic | `/v3/serp/google/organic/live/advanced` | `organic` | Organic result group rank only |
| Google Local Pack | `/v3/serp/google/organic/live/advanced` | `local_pack` | Map pack *on the organic results page* |
| Google Maps | `/v3/serp/google/maps/live/advanced` | `maps_search` | Google Maps results, not a Local Pack result |
| Google Local Finder | `/v3/serp/google/local_finder/live/advanced` | `local_pack` | Expanded Local Finder listings; item type alone does **not** make it a Local Pack result |

A business might rank differently in Maps, Finder and the organic Local Pack at exactly the same coordinate. The adapter **does not silently combine these values**.

DataForSEO's organic query accepts `location_coordinate="latitude,longitude,radius_m"`; Maps and Local Finder use `"latitude,longitude,zoomz"`, with distinct provider API request controls. The pilot accepts 7-decimal coordinates, 199–199999 meters for organic radius, zoom 4–18, explicit mobile/desktop, English locale (`en` or `en-US`) and depth 1–100. Unsupported parameters are rejected, not discarded or rounded. It never sends automated Google requests directly.

Source links:
- [DataForSEO four local surfaces, examples, and GPS targeting](https://dataforseo.com/help-center/track-local-pack-local-finder-and-google-maps-rankings-with-api)
- [Organic API](https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/)
- [Local Finder API](https://docs.dataforseo.com/v3/serp-google-local_finder-live-advanced/)
- [Google automated traffic policies](https://developers.google.com/search/docs/essentials/spam-policies)

## Implementation

Source: `app/services/dataforseo_geo.py`

`prepare_dataforseo_request(mf021_cell, surface, depth=20, zoom=14)` accepts only an MF-021 matrix cell with a coordinate-aware DataForSEO provider profile (ID starts `dataforseo-`) and a matching source family. It revalidates the SHA-256 signature of the MF-021 canonical `request_json`, rejects mislabeled/geographically unsupported cells and builds a deterministic vendor endpoint plus single-item JSON array payload. The request retains:

- Cell ID and original MF-021 request signature (research plan lineage).
- A separate SHA-256 vendor request signature of exact HTTPS endpoint and JSON bytes.
- Keyword, explicit localization string, device, language, depth, trace tag; NO token or API password.
- `execution_performed=False` always. This type **cannot dispatch** a request.

`inspect_dataforseo_response(prepared_request, untrusted_json, owner=TrackedBusiness(...))` validates a single completed provider-shaped response in memory. It requires both top-level/task success `status_code=20000`, `tasks_error=0`, exactly one task/result, correct URL path, full exact task echo of keyword, location, language, device, depth/tag, result family and timezone-aware observation time. Bounded items and strict ranks exclude paid ad placements; conflicting local place IDs/positions and duplicate surface rank are rejected. Costs from task and envelope must match and convert *exactly*, with no rounding, to integer microUSD. Missing ranks produce `unknown_not_in_returned_items`—not an unsupported "business not on Google" claim.

**Identity:** Organic owner recognition is based on the exact known website domain or a real subdomain with domain-boundary match. Local Pack/Finder use a known CID; Maps uses CID or place ID. Display names, logo similarities, URLs to Google redirects and an alleged owner's business name cannot independently establish owner identity or merge two locations. Suspicious/unparseable data fail closed rather than guessing. Other ranked listings remain independent candidate observations.

**Provenance:** The parser returns `owner_imported_unverified`, `independently_verified=False` and `provider_calls_made=0` even if a forged upload includes `verified:true`, a fake task ID or a plausible timestamp. A task ID and SHA-256 content digest are useful for correlation/deduplication, **not cryptographic proof of a provider call**. Neither a pasted JSON nor this parser alone can create MF-021 `SourceObservation(evidence_class=VERIFIED_PROVIDER)` or write a customer's official rank.

## What remains to connect real Google ranking measurements

1. Approved and licensed DataForSEO vendor account, allowable API/derived-data display terms, known tariff. Vendor requests cost money and should never execute without an owner-approved microUSD budget with idempotent reservation and a stored cost-vs-reserve reconciliation (see MF-036, still awaiting independent review).
2. Server-only HTTP adapter: fixed `https://api.dataforseo.com` origin, exact path allowlist, Basic Auth in environment-managed secret storage, fixed network timeout, no redirects or arbitrary URLs, response byte cap, vendor content/ID receipt stored with access-controlled envelope. Do not trust account-provided URL, IDs or "verified" flags.
3. Transactional execution gate: org_id, member consent, signed plan payload, monthly cap, maximum distinct requests and concurrency; no automatic retry after an unknown charged outcome. Reserve BEFORE dispatch, settle against independently observed vendor cost, preserve failed/unknown outcomes. No default live mode.
4. Actual response normalization: authenticate receipt provenance through the transport, map recognized provider surfaces to *distinct* data models, store immutable raw provider response access-controlled and a derived timestamped view. Do not relabel Maps/Finder as `LOCAL_PACK_SERP`; MF-021 SourceObservation currently only models organic and Local Pack, so it must be extended or accompanied by explicit Maps/Finder types before an official source-backed report can include them.
5. Connect the accepted provider observations to MF-049 Local Search Map and Next-Step Intelligence. Current owner UI imports **unverified** SERP CSV and displays schematic coordinate samples. Future server-signed provider claims, retained historical trend and competitor/place reconciliation need their own reviewed API. Prior completed MF-049 is currently awaiting independent review, so this task deliberately does not edit it.
6. Automated tests with mocked HTTP first, then owner-approved HTTPS staging with fictional tenant and a cost-capped, consented single-coordinate/keyword smoke test. Verify actual device/locale/location echoes and compare same surface, phrase, location and date across Prescott and Prescott Valley. Do not confuse requested location parameters with proof of searcher's physical GPS.

## Focused verification

```powershell
python -B -m pytest -q tests/test_dataforseo_geo.py tests/test_local_discovery_matrix.py tests/test_local_rank_insights.py -k 'not authenticated'
python -B -m ruff check app/services/dataforseo_geo.py tests/test_dataforseo_geo.py
python -B scripts/run_disposable_pg_audit.py --full-suite
python -B scripts/secret_scan.py
```

Never deploy, commit credentials, spend credits or claim live rank evidence on the basis of this contract. Independent board review required before moving the provider into live execution.


## Development verification — November 16, 2026 (Arizona)

- **121 focused tests passed** with no failures: DataForSEO adapter, MF-021 geographic matrix, and Local Search Map import/analysis (the authenticated endpoint test is included in the full suite).
- **881 PostgreSQL-backed integration tests passed**, zero skipped, zero errors, zero failures: `CI_GATE_PASS`, `FULL_POSTGRES_SUITE_EXIT=0`. The disposable database container stopped and was removed.
- `python -B -m ruff check app/services/dataforseo_geo.py tests/test_dataforseo_geo.py`: all checks passed.
- Every sample URL, task ID, business name and rank in tests is synthetic; no real vendor request, API credential, customer record, private browser sign-in, external payment, scraping or deployment was involved.
- The provider-only parser is not a complete online authorization or metering interface. Reviewer should pay particular attention to response parameter echoes, missing/ambiguous listing identity, surface distinctions, real supplier schema drift and microUSD cost handling before anyone authorizes vendor execution.
