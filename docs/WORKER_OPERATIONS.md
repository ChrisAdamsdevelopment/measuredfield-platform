# MF-010 — Reliable queued work and failure visibility

**Scope:** job scheduling, retries, worker process and operator-shell monitoring. No production deployment, live provider requests, customer emails, new credentials, migrations, or public/admin HTTP endpoints are introduced by this story.

## Problem and expected behavior

The queue is PostgreSQL-backed. `enqueue` inserts one job per caller-supplied idempotency key; workers use `FOR UPDATE SKIP LOCKED` and a ten-minute running lease. An individual email delivery already checks a locked outbox row and will not resend a message whose delivery is **recorded as sent**.

The original lead-notification handler, however, minted a fresh outbox message every time its own job was replayed. If the notification creation transaction committed but the job acknowledgment did not, this could queue redundant notices. MF-010 now locks the same lead row and reuses an existing `lead_notice` message for the same organization, recipient and stable inquiry URL before queueing an idempotent `send_email` job. The match also recognizes previously queued legacy notices when the site's public base URL changes.

This avoids duplicate *notification records* on handler replay. The existing `email.deliver` prevents a second send when the same message has a committed `sent` state.

**Provider acknowledgment limit:** If an external email provider actually accepts a request but the worker loses the connection or crashes before recording success, this platform cannot independently prove whether the provider delivered it. A retry can still duplicate an *ambiguous external acceptance*. True end-to-end exactly-once delivery requires provider-side idempotency/reconciliation. Do not promise it without a provider that explicitly supports and verifies the guarantee.

## Queue behavior

- **Retries:** at most `MAX_ATTEMPTS = 5` claims per job. On a handler failure, queue reattempts are backed off with `min(900 seconds, 30 × 2^attempts)`. Exhausted work becomes `failed` and is never silently discarded.
- **Interrupted workers:** `running` jobs whose lease is older than ten minutes may be reclaimed when attempts remain. Exhausted `queued` or stale `running` jobs become terminal `failed` without a sixth handler execution. A worker finishing late may update the job record **only while the row is still `running` at the exact attempt number it claimed**; finalization locks the row and discards outdated attempts instead of overwriting newer or terminal state.
- **Privacy:** raw exception strings are not saved in job errors because email providers may echo customer content or tokens. The queue stores the safe exception class or an internal fixed error label. Logs carry job ID/kind/attempt and exception class, never payload or provider exception text.
- **Restart:** the worker clears a previous in-process shutdown flag on normal start. SIGINT/SIGTERM exits after the current bounded work batch and does not silently abandon an already-started handler.
- **Authentication/tenant boundaries:** queue status is only a local operator-shell command under the runtime database credentials; no tenant or anonymous web endpoint is installed.

## Operator queue status

With the worker's authorized environment configured (`DATABASE_URL` set and `MF_INLINE_JOBS=false`), an operator can inspect the status **without processing jobs**:

```powershell
python -B -m app.worker --queue-status
```

The JSON report contains a UTC observation time, counts of `queued`, `running`, `done`, `failed`, number currently due, number of stale leases, and up to twenty recent failed-job IDs with allowlisted kind, attempt count and finished timestamp. It intentionally omits organization IDs, recipient identities, job payloads, idempotency keys, private errors, email bodies and credentials.

A job with status `failed` requires an authorized operator to investigate the underlying source and delivery history before deciding whether to retry it. Do not blindly requeue unknown-delivery email requests.

## Synthetic verification

`tests/test_worker_reliability.py` exercises retry bounds, safe error reporting, stale lease recovery, exhausted-job terminal status, late-worker attempt fencing, lead-notice replay suppression, recorded successful delivery replay, privacy-safe status output and graceful worker restart. Mock providers only.

The targeted tests that rely on `pg_database` need the disposable PostgreSQL test runner, not the developer's live database. The final release gate is:

```powershell
python -B scripts/run_disposable_pg_audit.py --full-suite
```

This verifies FORCE-RLS and the full application suite with no skipped tests and removes the temporary database afterward.

## Remaining production release gates

Actual email sending still requires explicit owner/provider setup, verified sending-domain authentication, provider delivery receipts, and documented provider-specific retry/idempotency semantics. Neither this change nor the synthetic tests claim any real message was delivered.

## Verified implementation — 2026-11-16

- Complete disposable PostgreSQL CI: **579 passed, zero skipped, zero errors, zero failures**; `CI_GATE_PASS`, `FULL_POSTGRES_SUITE_EXIT=0`, temporary container stopped and removed.
- Independent Gemini Argon Medium final read-only code review: **APPROVE**, no outstanding findings after adding attempt-fencing and explicit lead tenant checks.
- All email delivery tests used synthetic providers and fictional data; no real email was sent, no provider credentials accessed, and no staging/production deployment performed.
