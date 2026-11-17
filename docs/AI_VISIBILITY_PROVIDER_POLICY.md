# MF-022 — AI Visibility & Citation Research Provider Contract

Status: **local, deterministic preflight and receipt-validation contract**. It does not call OpenAI, Anthropic, Google, or any other live model. No API key, billable request, remote account, queued job, public endpoint, production database write or customer data is used.

## Owner-facing wording

Speak in ordinary business terms. **AI brand mentions** = provider-verified completed answers that explicitly mention the tracked brand. **Brand website cited** = a verified response with a returned source citation linking to the business's own domain. **Research questions** = actual prompts selected for the experiment. **Search area** = approximate requested market context where supported. **Last checked** = provider response timestamp. **Not checked** = a plan only, not an observation. **Evidence source** = actual provider reply, owner import, historical copy, reconstruction, or demonstration. Never say 'AI rank #1' when the evidence only reports a mention or link.

## Contract implementation

- app/schemas/ai_visibility_run.py: immutable Pydantic v2 models for market, question, provider capabilities, plan, completed/failed/unknown observations, trusted server-side receipt, and summary with explicit rates and unknowns.
- app/services/ai_visibility_adapter.py: pure functions that create a reproducible query x market x provider x repetition plan; preflight hard request and conservative owner-entered cost limits; deterministic IDs and idempotency keys; validate specific model/provider/request signatures; and summarize only authenticated completed receipts in verified denominators.
- tests/test_ai_visibility_adapter.py: synthetic evidence, budget, source isolation, no-network and identity-provenance regressions.

## Provider location claims

- **OpenAI Responses web search:** official docs show optional approximate location by city/region/country (plus other supported parameters depending on API shape). This is not precise GPS search impersonation or a guaranteed local Google ranking.
- **Anthropic web search:** official docs permit approximate city/region/country/timezone. This does not mean a latitude/longitude or specific device is reproduced.
- **Gemini Google Search grounding:** grounding returns search/citation metadata on supported API modes. No direct coordinate/radius localization is represented in this module. A city can be included as prompt context, labeled *prompt context only*, NOT provider-targeted geographic search.
- **Custom providers:** no unreviewed claim of location support. New API-specific capabilities require independent contract verification and tests.

References (verified November 2026):
- https://developers.openai.com/api/docs/guides/tools-web-search
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
- https://ai.google.dev/gemini-api/docs/google-search

## Provenance rules

1. ProviderObservation always records exact planned request ID, SHA-256 request signature, provider ID/model, timezone-aware observed_at, receipt ID, origin and completed/failed/unknown status.
2. An observation whose origin says verified_provider_receipt remains an **unverified claim** until a separately trusted backend validates the actual provider response and generates a matching TrustedReceipt. No browser/client-provided origin flag is proof. No backend verifier is connected by MF-022.
3. Verified completed responses with known true/false brand mentions enter the mention-rate denominator. A completed response with unknown mention status is excluded, not counted as 'no mention'. Unknown/failed runs, unverified receipt claims, owner imports, historical records, reconstructions and synthetic examples are excluded.
4. Brand website citation rate includes only verified completed responses from declared citation-capable providers. Only provider-sourced citation URLs under the exact brand domain or its subdomains qualify. A mention, a plain-text URL or a third-party link is not automatically counted as an owned-site citation. Citation rates use the separately reported eligible citation count.
5. Replayed/duplicate observations, unknown run IDs, tampered models, mismatched request signatures and duplicate trusted receipt proofs fail closed. Brand name/domain are bound to the experiment specification and idempotency keys.
6. A saved experiment plan is not an execution log. The explicit plan flag is live_execution_performed=false; neither a date on a plan nor a count of planned requests becomes proof of a live provider search.

## Budget and execution gates

- Every provider profile requires an explicit positive owner-supplied max_charge_per_run_usd and bounded max_web_search_uses. These are conservative planning assumptions, **not current provider pricing or an enforceable provider billing limit**.
- Full Cartesian product size must fit max_planned_runs; sum of owner-entered per-request cost ceilings must fit max_estimated_total_usd. Otherwise the planner refuses the entire experiment before any side effect.
- Repetitions have distinct idempotency keys; rebuilding the same specification yields the same run IDs; changing the tracked business identity produces different IDs.
- Providers can internally trigger multiple searches per model request. The actual adapter must enforce available tool-use limits, reconcile billed usage, and honor a real persistent tenant budget before making any paid request. MF-022 has neither outbound provider execution nor a persisted budget ledger.
- A real provider adapter must preserve provider response metadata/annotations per model/API version, obtain owner consent, verify workspace ownership/tenant role and CSRF, and issue server-only TrustedReceipt objects after verifying actual responses. Never accept a customer-supplied TrustedReceipt as authentication proof.

## Follow-on integration tasks

1. Implement one owner-approved provider adapter in a backend service, with official SDK/API version, actual grounding/citation parsing, timestamped receipt, per-tenant entitlements, billing caps, retries and idempotent queued execution. Validate generated query count, not merely number of prompts.
2. Persist receipts and source labels under tenant FORCE RLS, with explicit retention and legal/provider usage restrictions.
3. Render sampled coverage, unknown counts, confidence limits and citations in the owner UI. Offer a paid standalone research report without overstating observed brand prevalence or 'AI rank'.
4. Link MF-021 local business/organic SERP intelligence and MF-020 saved owner Search Console analysis while keeping those source types distinct.

## Tests

python -B -m pytest -q tests/test_ai_visibility_adapter.py
python -B scripts/run_disposable_pg_audit.py --full-suite

No live API calls, web scraping, credential access, customer files or production publishing were performed by this task.

## Immutable receipt-to-response binding

Before any verified rate is calculated, an independently authenticated backend must issue a TrustedReceipt that includes SHA-256 of the entire validated ProviderObservation (provider identity, model, request signature, timestamps, answer status, brand mention classification and citation URLs). The report recalculates that digest; if answer content, citations, timestamp, or associated receipt metadata change, aggregation fails closed with UntrustedReceipt. SHA-256 integrity alone is **not** a signature or proof of provider authentication: the trusted server-side verifier must not let a remote user supply, forge, or approve its own TrustedReceipt. MF-022 itself creates no trusted provider receipt and calls no live API.
