# MF-036 — Owner-authorized research budgets and durable no-dispatch reservation gate

**Status: LOCAL DEVELOPMENT ONLY / NO LIVE PROVIDER REQUESTS / NO CHARGES.**

MF-036 builds on the approved MF-035 OpenAI Responses `web_search` offline request adapter. It adds explicit owner approval, monthly spending reservation ceilings, PostgreSQL tenant protection and durable idempotency. **It does not send an API request.** The existence of a reservation is NOT evidence that research was performed, billing occurred, or a search result was verified.

## Owner journey

1. Signed-in owner/admin with an active workspace and recorded verified paid ServiceOrder (not a free consultation or an operator-provisioned unpaid pilot) chooses a UTC calendar-month research allowance, from USD $0 to $500 (pilot limit), and explicitly submits `AUTHORIZE RESEARCH RESERVATIONS`. This is **permission to reserve**, not permission to execute paid searches.
2. Choose an exact previously built MF-022 `ExperimentSpec` and the SHA-keyed run ID. Approve `RESERVE THIS SEARCH` explicitly. The server reproduces the entire experiment plan and MF-035 request signature. If a run is altered or the monthly allowance is insufficient, nothing is reserved.
3. Each reserved run uses one deterministic idempotency key. A retry of the identical authorization returns the same reservation and does not add another hold.
4. Cancel an unsent reservation with `CANCEL UNSENT SEARCH`. Cancel is itself idempotent. A run that was ever dispatched, or whose dispatch charge is unknown, **cannot be cancelled or released** by the owner endpoint until a separate financial reconciliation workflow confirms the actual outcome.
5. Budget responses show *held estimated micro-USD* and *reconciled micro-USD*, NOT actual billed spend. The module currently has no trusted provider billing feed: reconciled amount remains zero.

## Files

- `app/models.py`: `VisibilityResearchBudget`, `VisibilityResearchReservation`; strict nonnegative and ceiling constraints; same-tenant composite foreign key; two entries in `TENANT_TABLES` and `TENANT_MODELS`.
- `migrations/versions/0008_visibility_execution_budget.py`: both tables, unique org/month and org/idempotency keys, FORCE row-level security and index for reservation status.
- `app/services/visibility_execution.py`: membership and paid-plan revalidation, exact Decimal to integer micro-USD (no float inputs), serializable budget reservation using PostgreSQL row locks, idempotent replay, decline below outstanding holds, cancel unsent only.
- `app/routers/visibility_execution.py`: development-only signed-in JSON endpoints protected by session authorization and X-CSRF-Token; no client-supplied org identity is used. Status changes create privacy-minimal audit records. No transport or provider key.
- `tests/test_pg_visibility_execution.py`: real disposable PostgreSQL RLS, subscription and CSRF checks, exact money, reservation concurrency, idempotency, cancellation, unknown outcome and no-network assertions.

## Security and accounting invariants

- All research tables have `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`. Each row belongs to the currently authenticated org, and the second table has a same-org composite foreign key into the budget row.
- Reservation and budget reauthorization use `SELECT ... FOR UPDATE` on the current budget row; concurrent requests for different run IDs cannot exceed the approved total. The unique `(org_id,idempotency_key)` constraint prevents duplicate reservations even if callers retry concurrently.
- Every reservation is a **forecast upper estimate**, not a provider-enforced price. Future live execution must enforce an actual provider monetary quota independently and reconcile charges, not merely rely on `max_tool_calls` to protect USD.
- The stored monthly period is UTC; amounts are integer **micro-USD (1 USD = 1,000,000 micro-USD)**.
- Refundable status is **reserved** only. A future dispatcher must, in its own committed transaction, atomically move the run to **dispatch_unknown** *before* sending any bytes. After dispatch, never automatically replay a request, release the hold or label the response verified on a timeout or HTTP error. Network I/O must occur outside any request or database transaction.
- Only a separately authenticated provider transport may create a `TrustedReceipt` after validating actual response and usage. MF-035 structural inspection explicitly cannot mint one. Client-imported JSON, reconstructed historical answers, screenshots or model-generated anecdotes are not provider verification.
- Only one provider family is supported in this first slice: OpenAI hosted web search with approximately localized market, not device GPS, Google Local Pack ranking, or the user's ChatGPT account.

## Explicit release blockers

- No live-search feature flag is enabled and the production router responds 404.
- No business provider secret/SDK is read; no API calls, payments or background jobs happen from this module.
- Provider pricing and cost-of-usage account reconciliation, independent merchant invoice reconciliation, operator approved price schedule, per-tenant entitlement from provider payments, and provider response signature/receipt minting are **future tasks**.
- A future execution worker must verify actual plan/brand/owner consent against persisted immutable approval before sending, implement a durable dispatched/unknown reconciliation and bounded provider HTTP transport. This story intentionally stops at reservation.

## Verification

- `python -B -m pytest -q tests/test_pg_visibility_execution.py` within the disposable real PostgreSQL runner.
- `python -B scripts/run_disposable_pg_audit.py --full-suite` for zero skipped and production schema/alembic checks.
- `python -B -m ruff check app/models.py app/services/visibility_execution.py app/routers/visibility_execution.py tests/test_pg_visibility_execution.py migrations/versions/0008_visibility_execution_budget.py`.
- `python -B scripts/secret_scan.py --staged` and `--tracked`.

No real customer data was read or sent and no actual company spending limit was changed.

## Independent-review remediation — November 16, 2026 Arizona evening

Claude's independent review of commit `c466f9c` **requested changes** after reproducing a concurrency defect and untrusted owner-declared charge ceilings. The following local revision is a security remediation, **not independent acceptance or production approval**.

1. **No double release under simultaneous cancels.** Cancellation acquires the budget lock, then re-selects the reservation with `FOR UPDATE` and `populate_existing=True`, overwriting the earlier SQLAlchemy identity-map version. Status is checked only after that refresh. A stressed real-PostgreSQL regression reserves three independent holds, races six cancellations on one, and requires exactly one winner and `held_micros == SUM(remaining reserved ceiling_micros)`, never an undercount.
2. **No client-created price floor.** Because there is not yet an authenticated provider tariff and charge reconciliation, this development pilot only accepts the explicitly server-approved offline **synthetic** OpenAI model profile `synthetic-openai-model`, with a conservative local test minimum `250000` micro-USD/run and at most two planned web searches. This is a *testing floor*, **not an assertion of real OpenAI API rates or a provider-enforced maximum**. Client-provided forecasts below the floor and unapproved models fail closed; higher owner ceilings still must match the signed experiment and held record. A different forecast on the same planned request conflicts instead of silently reusing an old hold. No real paid provider dispatch is permitted until a separately approved model-price catalogue and independent usage-metering are implemented.
3. **Honest replay status.** After an owner cancels a reservation, an attempt to replay the same planned search returns `409 already cancelled`, not a misleading successful unreserved search.
4. **More precise refusal and decimal grammar.** Research authorization failures return HTTP 403; validly formed but unsafe budget/plan payloads return 422. Money strings are restricted to ordinary ASCII decimal digits with at most six fractional digits (no underscores, exponent notation, floats, booleans, or misleading alternate literal forms).

These changes are limited to the existing service/router, its real PostgreSQL regression tests and this internal design document. No billing, live provider token, external search, financial reconciliation, customer records, deployment or live account changes were performed. Independent reviewer must validate concurrency behavior and the server policy before this is considered approved.

### Verified local security regression result

- Real disposable PostgreSQL **full application suite** after concurrency and server price-floor fixes: `CI_GATE_PASS` — **807 passed**, **zero skips/errors/failures** (251.51 seconds); disposable PG container stopped and removed.
- New regression: eight repeated batches of six simultaneous cancellation calls on one of three $0.25 holds; exactly one cancellation succeeds per batch, all others become idempotent no-ops, and held micro-USD remains equal to the two remaining reserved rows.
- New rejection coverage: owner-supplied per-run price $0.0001 cannot reserve, unapproved model cannot reserve, changed forecast ceiling on an identical planned call conflicts instead of reusing a hold, and money strings like `5_0` / exponent notation are rejected.
- The only authorized model profile in this LOCAL test remains `synthetic-openai-model`. A real provider cannot be reserved or dispatched via this slice. Before restoring real provider work, obtain an independent price schedule and enforce charge accounting in the dispatcher.
- MF-036 is still pending independent re-review; no production deployment occurred.
