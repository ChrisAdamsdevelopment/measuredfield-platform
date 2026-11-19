# MF-073 — Census Market Intelligence: live API evidence and read-only adapter

**Local engineering checkpoint:** 2026-11-17, Prescott Valley, Arizona.
**Status:** implemented read-only server adapter and explicit opt-in live smoke; NOT yet an owner dashboard, ChatGPT tool, production deployment, cross-tenant API credential vault, named-business directory, or automatically updated market forecast.
**Branch:** single canonical mf_platform/main.
**Release:** independent code review required before wider use.

## Purpose and market analysis

MeasuredField's Geo/Business Intelligence should answer grounded questions such as: How many households are in this town? How many **employer establishments** operate in construction across a ZIP or county? How many **nonemployer establishments** operate in construction across the county? How do those statistical aggregates compare against observed geographic search ranking, Google Business Profile and named competitor datasets once those separate services are authorized and implemented?

The Census API provides **historical aggregate statistics**, not live rankings, store listings, current ROC licenses, business phone numbers or real-time registered contractor counts. Census ZIP business geographies, ACS ZCTAs, Census places and Yavapai County have DIFFERENT boundaries and must never be presented as interchangeable maps.

## Official sources and verified query shapes

- **2023 County Business Patterns (CBP):** https://www.census.gov/data/developers/data-sets/cbp-zbp/cbp-api.html
  - Endpoint: https://api.census.gov/data/2023/cbp
  - NAICS field: **NAICS2017**. Filters **LFO=001**, **EMPSZES=001** total classes to avoid accidentally taking the first of multiple size/legal-form bands.
  - **county:** for=county:025 and in=state:04; fields ESTAB, EMP, PAYANN.
  - **business ZIP:** for=zip code:86314; fields ESTAB. This is the Census business ZIP, **not** ACS ZCTA and not the Prescott Valley town boundary.
  - Employer *establishments* are not synonymous with independent companies, licensed contractors or all sole proprietors; PAYANN is published in **$1,000** units.
  - The 2023 CBP API documentation explicitly requires an API key.
- **2024 Nonemployer Statistics (NES):** https://www.census.gov/data/developers/data-sets/nonemp-api/2024.html
  - Endpoint: https://api.census.gov/data/2024/nonemp
  - NAICS field: **NAICS2022**, county and state FIPS, total legal and receipt-size classes (LFO=001, RCPSZES=001).
  - Fields NESTAB and NRCPTOT. Receipts are published in **$1,000**, not profit. Nonemployer excludes businesses with paid employees; independent contractors need not be Arizona licensed contractors.
- **2024 American Community Survey, 5-year (ACS):**
  - Endpoint: https://api.census.gov/data/2024/acs/acs5
  - **B01003_001E** estimated population; **B11001_001E** estimated households.
  - for=place:* & in=state:04 to discover Prescott Valley's actual place FIPS: **57450** (geography code 0457450), or for=place:57450 with in=state:04. County and ZCTA variants supported.
  - This reflects ACS multiyear estimates, not a 2024 Census headcount or real-time residences; ACS sampling error requires further margin-of-error treatment before precise demand modeling.

The current backend intentionally does **not** implement the ABS dataset from the initial research. ABS variable/year/geography eligibility should be independently checked and added as a separate researched story. The first priority is CBP/ZBP + NES + ACS and correctly bounded, source-backed owner charts.

## Confirmed live smoke (Nov 17, 2026; key never logged)

User approved accessing the activated local file at **D:\CENSUS_API_KEY.env**, not the verbally remembered spelling "Census API Key.env". The file remains outside all source Git trees. Its single key passed local 40-character validation. Four direct TLS Census requests succeeded:

| Dataset | Reference geography | Variable | API response | Units |
|---|---|---|---:|---|
| CBP 2023, NAICS2017=23 | Yavapai County, AZ FIPS 04025 | ESTAB | **971** | employer establishments |
| CBP 2023, NAICS2017=23 | Yavapai County | EMP | **6,829** | paid employees |
| CBP 2023, NAICS2017=23 | Yavapai County | PAYANN | **387,208** | thousands of USD (approx. $387.2M) |
| CBP 2023, NAICS2017=23 | ZIP 86314 | ESTAB | **148** | employer establishments |
| NES 2024, NAICS2022=23 | Yavapai County | NESTAB | **2,618** | nonemployer establishments |
| NES 2024, NAICS2022=23 | Yavapai County | NRCPTOT | **228,091** | thousands of USD (approx. $228.1M gross receipts) |
| ACS 2024 5-year | Prescott Valley town, AZ | B01003_001E | **49,179** | estimated persons |
| ACS 2024 5-year | Prescott Valley town, AZ | B11001_001E | **20,968** | estimated households |

These live figures are public aggregates for different reference periods; **do not sum CBP + NES as an exact current market size or interpret them as actual online local competitors**. A company can have multiple establishments. For actual business names and license status, separate approved Google Places/Arizona ROC sources are required.

**Two additional live endpoint checks:** the 2023 CBP Yavapai County *Residential Remodelers* industry (NAICS2017 **236118**) reported **125 employer establishments**, **426 employees**, and **19,468** thousand USD in annual payroll. The 2024 ACS 5-year **ZCTA 86314** reported **40,317 estimated persons** and **16,700 households**. ZCTA 86314 does not represent the same area as Prescott Valley town (which reported 49,179 persons and 20,968 households). This real contrast is why all UI cards must explicitly label geographic boundaries and cannot merge ZIP, ZCTA and incorporated place metrics.

## Safe runtime secret handling

Server adapter: app/services/census_market.py
Live script: scripts/census_market_smoke.py
Offline tests: tests/test_census_market.py

- **Local Windows pilot:** reads D:\CENSUS_API_KEY.env only when explicitly called. No key content in source, Git, command arguments, printed data, source URLs, exception messages or analytics.
- **Later deployed server:** provide MF_CENSUS_API_KEY through an approved secret store or MF_CENSUS_API_KEY_FILE pointing to an owner-approved protected file. Per-tenant self-service key vault is a separate MF-054 task; MF-073 does **not** pretend to implement it.
- Census's protocol uses the key as a query parameter **inside HTTPS**. That request target is never printed, saved, returned to browsers or given to an LLM. The public source URL returned by the adapter deliberately omits the key.
- Single hardcoded hostname **api.census.gov**, limited known dataset paths, strict requested-field/NAICS/geography construction, standard TLS certificate validation, no redirects, generic sanitized errors, 15-second timeout and 512KB bounded response.
- Maximum 900 output data rows, per-process 60-request budget by default and 1-hour TTL memory cache limited to 128 entries; the four-query live smoke is capped at exactly 4 requests. These are prototype safeguards, **not a distributed production quota or multi-worker rate limiter**.
- No customer data is sent with Census lookups; only public geography/industry predicates.
- Client preserves exact dataset name, NAICS edition, year, unit, geography type, place/ZIP/county ID, time fetched, cache status and key-free source URL.
- Missing/suppressed data or ACS numeric sentinels become None with suppressed_or_not_available, **never invented zero**.

## Owner-friendly use and reproducible verification

**Offline security/data tests:** python -B -m pytest -q tests/test_census_market.py

**Lint:** python -B -m ruff check app/services/census_market.py scripts/census_market_smoke.py tests/test_census_market.py

**Default smoke is offline and fails intentionally:** python -B scripts/census_market_smoke.py

**Explicit live smoke (owner authorized):** python -B scripts/census_market_smoke.py --live

The command prints only public aggregates and source metadata, never the key or a URL containing it. The live run reported CENSUS_MARKET_LIVE_SMOKE_PASS with 4 successful queries. The offline suite exercises wrong ZIP/place/county filter injection, corrupted data, suppressed records, bad key file formats, fake error messages containing secret-like text, request budgets, cache reuse and exact unit/year labels.

No new production route or owner dashboard was opened by this story. Implement the beautiful mobile/desktop market-intelligence panel in **MF-074** after this story passes independent review. Add read-only, tenant-aware ChatGPT Census market tools in **MF-075** only after MF-073 and MF-050 plus the MF-037 authentication gate are independently approved. For PBC and future MeasuredField customers, the dashboard should pair demographic estimates with SEO/geo evidence but preserve source dates and geographic boundaries, never label Census-derived business totals as actual named competitor lists.

## Remaining gates

- Peer reviewer inspects key redaction, resource allowlist, suppression, quota rules, source provenance and actual live evidence; reruns offline tests.
- Run full disposable PostgreSQL application regression, secret scan and Ruff on canonical main before submission. No live production change.
- MF-074: authenticated owner page, good UX with meaningful Census geography comparison, safe cache/loads and rate limits with controlled fake providers.
- MF-075: MCP read-only request tools with proper owner/tenant scope; no API key sent in tool results.
- Enterprise-scale usage: per-tenant enabled/disabled controls, distributed quotas/caches, error monitoring that NEVER logs request targets, key rotation, licensing/attribution review, and security testing against upstream API/HTTP library failures.
