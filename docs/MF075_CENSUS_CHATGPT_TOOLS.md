# MF-075: Read-only Census market intelligence from ChatGPT

**Local implementation, pending independent review and production connection.** This story builds on MF-037 verified OAuth resource identity, MF-073 Census adapter, MF-074 dashboard and MF-050 owner connection UX. It does not create a new OAuth provider, publish a plugin, or expose credentials.

## Tools

1. `list_supported_census_markets(business_id)`: authenticated owner must be authorized for the exact existing MeasuredField business. Returns only three fixed Prescott-area pilot market choices (Prescott, Prescott Valley, Chino Valley), with separately labelled Census places, CBP business ZIP codes and ACS ZCTAs; no arbitrary latitude/longitude, ZIP or address queries.
2. `get_census_market_snapshot(business_id, market_id)`: the same OAuth `mf:read` and linked-subject checks, current active membership and rate limits before up to six bounded official aggregate Census queries. Returns source-labelled observations with dataset, vintage/reference year, NAICS code and edition where applicable, precise geography kind/name, metrics, availability, units, retrieval timestamp, verified public source URL and explicit caveats. Missing sources report `not_loaded`/empty metrics, never invented zero counts.

The MCP server registers these tools with `ToolAnnotations(read_only_hint=True, open_world_hint=False)` and the same OAuth `securitySchemes` as existing tenant tools. The server-side `identity()` re-verifies JWT authorization before calling either adapter function, even in non-HTTP tool invocation.

## Data and security contract

- Census API credentials are loaded exclusively by the approved server adapter via `MF_CENSUS_API_KEY` (or the existing secured local key-file loader), never included in tool schemas, model prompts, logs or return values.
- The market result includes only public aggregates. It does **not** represent named competitors, Google/AI visibility, live contractor counts, market share, business saturation percentages or revenue forecasts.
- Business authorization comes from the verified OAuth issuer+subject link and active MeasuredField tenant membership; no business ID or user ID supplied as a tool argument becomes an authentication claim.
- Before each actual Census snapshot, the server checks active-user membership and atomically increments two PostgreSQL fixed-window counters: 15 per user and 40 per business per hour, using a 3,600-second window. A quota overrun denies the snapshot; it cannot be bypassed by changing `market_id`. Unlike the owner UI, the MCP integration does not collect browser CSRF tokens, because authentication uses MCP OAuth bearer verification, not cookies.
- `list_supported_census_markets` uses an already protected business overview read and makes no Census provider call.
- Every area query uses the existing MF-073 strict HTTPS endpoint allowlist, timeouts, max-call budget, malformed-response guards, private in-process cache and redacted source URL behavior.
- Both tools are read-only from the business/product perspective. The source query may write internal rate-limit accounting, but does not mutate tenant business records or write to external providers.

## Verification and launch boundary

Required: focused offline synthetic-provider tests, official MCP SDK 2.3.0 tool discovery and protected metadata, PostgreSQL tenant/security tests, Ruff, tracked/staged secret scans, Git diff, and independent Claude/Argon approval. A passing local test **does not** establish a ChatGPT connection.

The live Render service `measuredfield-production-web` (external URL `https://measuredfield-staging-web.onrender.com`) still used a November 13 release when checked. Its `/mcp` and `/.well-known/oauth-protected-resource/mcp` returned 404; OAuth resource and separate ChatGPT client registrations were not configured. Do not add `mcp.json` to the existing private plugin or set `MF_ENABLE_REMOTE_MCP=true` until MF-077 is deployed with correct Auth0 audience, consent, exact HTTPS host, connection-ready checks, and owner-approved release.

The plugin saved to ChatGPT is the existing private `plugins_6afcba53a45c8191b5c9511a991758aa`. When the real authorized endpoint is operational, update **that plugin**, preserving its identity, four skills, scope and permissions. Owner approval is required to complete authorization, then a cross-tenant refusal and Disconnect/revoke test.

## Focused implementation

- `app/mcp_plugin/census_market_tools.py`: source-safe DTO serialization and tenant quota.
- `app/mcp_plugin/server.py`: two read-only tool registrations.
- `tests/test_mcp_census_market_tools.py`: fake Census transport, authorization negative cases, provenance and partial coverage, quota rules.
- `tests/test_mcp_plugin_metadata.py`: includes the two new protected tools in metadata/schema inventory.

Source truth: Census ACS 2024 5-year, CBP 2023 and NES 2024 fields have distinct geographic coverage and are never silently merged. A stronger present-day competitor analysis must use separately verified sources; this tool alone cannot provide that.
