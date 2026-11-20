# MF-004 — Owner-reviewed customer inquiry replies (local implementation)

## Scope and safety
- Owner/workspace members with `leads.write` can save inert drafts against a tenant-owned lead. Only owner/admin role can approve, after a first-party CSRF-protected POST. This feature sends nothing from ChatGPT/MCP; MCP drafts remain separate and cannot call the outbound queue.
- Each response persists in `lead_replies` with composite lead/org foreign key and FORCE row-level security. Reply body, subject, destination and sender's approval identity are fixed at approval time; editing requires a new draft. The database enforces approval before non-draft states.
- No provider: save only, approval blocked. Provider enabled: the same original draft goes to a PostgreSQL idempotent job, whose worker separately rechecks approval and same-tenant row.
- Uses configured Resend transactional email provider and an opaque per-reply `Idempotency-Key` for retries. Resend's documented idempotency retention is 24 hours, so a job older than 20 hours after approval is stopped for manual reconciliation rather than blindly reissued.
- Provider response with a real message ID is recorded as **accepted**, not **delivered**. Real delivery/bounce/open requires a separate verified signed provider event pipeline; this slice does not claim one. Provider/network uncertainty is recorded as failed, never as delivered.
- Original inquiry first_response_at and contacted state change only after provider acceptance, never on draft or queue. Do not equate response attempts to customer conversation or delivery.
- Cross-tenant URL IDs return 404 in the existing authenticated tenant RLS session. Each action uses the normal role policy and session CSRF.
- Errors displayed/logged are generic; actual provider error payloads are not persisted. The reply record contains customer contact text and must be governed by tenant data retention/export/deletion.

## Workflow
1. Workspace `/app/inquiries/<id>`: save a draft (unsent) with subject and body.
2. Owner/admin reads the exact draft and clicks "Approve and queue this exact reply".
3. `send_lead_reply` job sends through configured Resend provider, with stable idempotency key and bounded retries.
4. Reply history distinguishes Draft, Queued, Accepted (delivery unverified), Failed, and Not configured.
5. If provider status is uncertain after the retention boundary, manual reconciliation is necessary before another send.

## Deployment and operations prerequisites
- Source changes and migration 0016 are **local only** until code review and controlled deployment. Never run migration on live database without verified backup/recovery, existing 0013–0015 migrations and DB role/RLS validation.
- Verify sender domain with Resend, configure `MF_EMAIL_PROVIDER=resend`, `MF_RESEND_API_KEY`/actual project config name, and `MF_EMAIL_FROM`/actual project config name in secret store. Confirm exact names from `app/config.py` before configuring; do not enter secrets in source or printed logs.
- Verify real provider sandbox/test behavior, worker restart/retry, request rate limits, deliverability, suppression and opt-out policies, and independent security review **before any customer email**. Do not enable production sending just because code has passed synthetic tests.
- Provider idempotency protects retries within the provider's time window, not exactly-once delivery forever. A network-timeout race must be reconciled from provider event/logs before a new send.
- UI is not a guarantee that the recipient received the email.

## Tests
`tests/test_lead_outbound.py` covers CSRF, inert draft, no-provider block, one approval and one synthetic provider acceptance despite replay, tenant isolation, fake failure and Resend HTTP idempotency header. Full disposable restricted-Postgres suite, Ruff and independent peer review remain release requirements.

## Security review resolution, 2026-11-19
- Single-owner approval is intentional: an authenticated owner/admin can create and approve their own outbound message. This is explicit in-product authorization, not an independent two-person workflow. AI-drafted or API-created replies do not by themselves authorize sending. In the eventual consent-granted agent, a bounded account-level send grant may provide authorization under separate operation policy.
- Worker throughput: Resend calls may hold a PostgreSQL connection and row lock for up to the provider HTTP timeout. Restrict sending job worker concurrency to fewer than available DB connections, monitor lock waits and connection-pool saturation. Refactor to claimed sending leases for multi-tenant high-volume sending.
- Provider idempotency is bounded; reconciliation is required after uncertain outcomes. Pending delivery webhook/DSN evidence remains future work.
- Independent Claude read-only source review requested changes (F1–F6). Single-owner policy and connection-limit decisions documented, database default, header NUL check and CSRF test corrected. No live customer send occurred.
