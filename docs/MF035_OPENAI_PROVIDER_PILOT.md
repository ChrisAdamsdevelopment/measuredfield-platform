# MF-035 — OpenAI AI Visibility request and evidence inspection boundary

Status: local, offline, synthetic regression-passing provider adapter **foundation**. This module makes **zero** API calls, charges **zero** money, imports no secrets, opens no business/customer records and does not claim verified real-world AI search. There is no public or authenticated execution route.

## Why this increment matters

MeasuredField's geographic research foundation (MF-021), saved GSC workflow (MF-020), and provider-neutral AI visibility planner (MF-022) already distinguish benchmark prompts, markets, locations, receipts, citation evidence, import provenance, and unknowns. Until MF-035, there was no provider-specific bridge to turn a signed MF-022 planned run into an OpenAI Responses request or inspect the returned web-search annotations. MF-035 implements that bridge *without activating the network transport*.

### Official provider contract checked November 16, 2026

- https://developers.openai.com/api/docs/guides/tools-web-search (OpenAI Responses hosted web_search, approximate user location using country/city/region/timezone, provider-issued url_citation annotations, completed web_search_call items, tool_choice required where searching is mandatory)
- https://developers.openai.com/api/reference/resources/responses/methods/create (max_tool_calls applies to all built-in tool calls and is a server-understood upper limit, not guaranteed cost/billing protection)

Do not represent city/region targeting as coordinate-precise geolocation, Google Maps local-pack ranking, local organic position, a residential device proxy or proof of what a specific human's ChatGPT account saw. No synthetic result is equivalent to a billable authenticated search.

## What the code does

**app/services/openai_visibility_pilot.py** exports two pure functions.

1. build_openai_web_search_request(spec, plan, run): first verifies full MF-022 plan equality and selected run object; rejects altered model, location, signature, provider family and search count; then constructs **one** exact Responses payload:
   - POST /v1/responses, model from reviewed provider profile, input from approved question;
   - tools containing the hosted web_search tool, optional approximate user_location exactly when supported;
   - tool_choice required, max_tool_calls equal to the planned per-run ceiling;
   - include web_search_call.action.sources for provider source visibility; store false.
   - Returned envelope includes experiment hash, run ID, request signature, idempotency key, planned cost upper estimate and the explicit flags requests_sent=0 / status=not_executed.

2. inspect_openai_web_search_response(spec, plan, run, raw): validates a bounded JSON object shaped like a completed response with matching model, ID and Unix timestamp; requires at least one completed search tool action, forbids duplicate tool call IDs and exceeding the planned tool-use ceiling; then extracts ONLY the url_citation annotations from assistant output, not URLs written as prose. The literal business-name mention is measured separately from website citations. Returns response SHA256, source URLs, count and short answer excerpt.

**Critically: inspector output is always tagged origin=unverified_provider_structure and eligible_for_verified_rates=false.** It cannot mint a TrustedReceipt. An owner-imported or user-forged JSON object cannot become independently authenticated merely because it has a plausible response ID or citations. Do not feed these previews into the verified citation/mention denominator in MF-022.

## User-facing language

- **Search questions:** what was planned to ask
- **Search areas:** approximate market city/region, not GPS
- **Not checked:** no provider request was made
- **Source links:** links inside a completed provider answer annotation, not proof of website rankings
- **Evidence:** synthetic, imported, historical, or separately server-verified
- **Brand mentioned:** the answer text includes the business's literal name
- **Your website cited:** separate source URL annotation matches the owned business domain
- **Unknown:** insufficient authenticated evidence for a claim

## Pending tasks before live provider execution

1. Independent Argon code review of MF-035, including API payload stability, canonical-plan equality, and citation parsing adversarial cases.
2. Authenticate a server-side, allowlisted model and provider SDK/HTTPS transport that never accepts an arbitrary URL, API key or trusted receipt from a browser.
3. Require current tenant membership, owner review/consent, CSRF, paid entitlement, persistent per-tenant reserve/debit ledger, enforced request count/tool cap, retries and idempotency across workers. Owner-supplied estimated ceilings are NOT provider price guarantees.
4. On verified genuine provider response returned by trusted server transport, preserve provider request IDs, timestamps and usage, issue server-only cryptographic provenance, persist under FORCE RLS and retention policy, and reconcile actual billing.
5. Add source-linked owner UI and sampling coverage counts. Never call a raw preview a live result or an AI rank.
6. Only then authorize separately-approved test provider calls. This story does not authorize a billable search.

## Verification

Focused: python -B -m pytest -q tests/test_openai_visibility_pilot.py

Full: python -B scripts/run_disposable_pg_audit.py --full-suite

Secret scan: python -B scripts/secret_scan.py --staged then --tracked

No new production credentials or live customer files are needed for this increment.
