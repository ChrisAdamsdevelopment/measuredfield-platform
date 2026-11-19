# MF-023 — Public website-copy preview: release and trust boundaries

## What this route actually does

The no-account `/visibility-preview` page gives a business owner an honest, lightweight taste of MeasuredField. A visitor enters a business name, industry, city or service area, and optionally a website address. The response compares **invented generic placeholder copy** with an **illustrative alternative** from MeasuredField's existing `sitegen.DEFAULT_HEADLINES` catalog. It supplies two **suggested phrases to research**, not genuine query-volume data.

The page visibly identifies the source, UTC generation time, website access status, search-position status, AI-citation status, and the fact that these are examples rather than measurements. It never asserts that its generic "before" text was extracted from a real website. It does not produce an authenticated screenshot or edited website.

## Owner experience

1. Open `/visibility-preview` without creating an account.
2. Enter a business name, service area, industry, and optional public HTTPS domain.
3. Explicitly acknowledge that the result is not a website scan or verified result.
4. Compare generic copy with an illustrative alternative.
5. See two search-phrase ideas labeled as **not measured**.
6. Choose the existing assessment pathway if more substantial help is wanted.

The optional website address is normalized and labeled, but it is never visited. Entering a domain is **not** proof of ownership or permission to fetch it. The follow-up assessment has its own separate contact consent.

## Security controls

- Reuses the existing public router and form/template rendering instead of introducing new network/provider clients.
- Uses the existing HTTP host/IP canonicalization and fixed-window PostgreSQL rate limiter, with a hard **8 sample submissions per hour per client key**. The counter is **committed for both success and rate-limit denial**; otherwise regular submission attempts would roll back and evade throttling.
- A hidden honeypot field causes a redirect without generating a result.
- Industry choices use the existing allowlisted `sitegen.INDUSTRIES` codes.
- Business and location inputs are bounded to 90 and 100 characters; oversized values receive errors rather than silent truncation. The optional website is limited to 250 characters and must pass the existing nonfetching public HTTPS URL validator.
- Jinja autoescape handles user-entered text; the input is never treated as HTML, a destination URL, executable code, or a provider prompt.
- `Cache-Control: no-store` prevents caching pages containing submitted business details.
- No user session, tenant organization, payment, account, lead, customer database row, local file, deployment, remote credential, or owner approval state is created by this route. Only metadata for the rate-limit counter is written.
- No live Google, OpenAI, Anthropic, Gemini or other provider API invocation. No website crawl, screenshot fetch, computer-connector access, public ranking assertion, paid search, or real account connection.

## Testing and deployment

Tests are in `tests/test_public_visibility_teaser.py` and cover public GET, illustrative POST, required acknowledgement, XSS escaping, invalid/private URL handling, no tenant/assessment creation, persistent throttling across separate HTTP clients, and honeypot behavior. Use the standard ephemeral PostgreSQL runner to exercise these integration tests under the restricted runtime role:

```
python -B scripts/run_disposable_pg_audit.py --full-suite
```

Do not announce this feature as publicly live until a separate staging review confirms navigation, accessibility, domain/DNS, permissions, CSP, and production rollout. This story only implements locally tested application code.

## Future bridge to the true visual comparison

The existing authenticated MF-014–016 visual-preview pipeline uses explicit owner-attested brand media, safe media tokens, and an interactive preview. To bridge from this public sample to that real workflow:

1. Offer the owner a separate consent and ownership-verification step.
2. Run an authorized, egress-restricted, SSRF-pinned source fetch; never fetch a mere optional URL from this form.
3. Capture and label the actual before-state and document which observations are measured, owner-entered, inferred, or unknown.
4. Produce a separately approved interactive after-preview using authorized media.
5. Require explicit approval before editing files or publishing anything.

Public teaser samples should never be counted as real website scans, ranking positions, verified AI citations, business leads, or completed remodeling/marketing outcomes.
