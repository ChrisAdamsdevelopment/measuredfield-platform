# MF-094: Owner-shared takeoff evidence

## Implemented interface

Existing protected /takeoff -> Review -> Share current takeoff with ChatGPT (explicit consent).
The same deterministic application builds the review packet and executes estimate assurance.
POST /api/takeoff/shared-reviews stores an immutable, bounded, validated snapshot in existing
FORCE-RLS tenant Documents, with an application-reserved content type. No migration or new paid
provider. Workspace documents are accessible to business members through existing documents UI;
MCP reads require the same sharing owner, active membership, current signed OAuth link and
pilot allowlist. No automatic egress, PDF, raw geometry, cost totals or customer-name fields.
Notes may still contain sensitive text; user must review before sharing.
Snapshot payload excludes unstructured code basis and client instructions. Manifest inconsistencies
force blocked readiness; client-reported evidence is not a server-verified measurement.
Max 512 KiB per snapshot and 20 per business, content-hash deduplication.
GET /api/takeoff/shared-reviews lists own receipts; DELETE revokes future retrieval, not prior chats.

MCP SDK 2.3.0 tools with existing mf:read scope:
- list_takeoff_reviews(business_id)
- get_takeoff_review(business_id,snapshot_id,section="summary",offset=0)
Sections: summary, drawing_register, printed_work_areas, scope_items, measurements,
field_entered_quantities, material_lines, price_research_queue, warnings, findings, dispositions.
Read max 20 records per call; next_offset is null at end. No customer contact, estimate edits or bid approval.

## Evidence and limits

2026-11-25: 30 focused pure/route/metadata tests passed; 9 focused PostgreSQL tests passed.
After manifest hardening, 27 tests under the actual pinned SDK 2.3.0 and disposable real PG passed,
including signed HTTP tools/list and tools/call, a real stored snapshot, cross-business denial,
CSRF/consent denial and revocation. Antigravity Argon Low reviewed the supplied service and
application diff (process 23292, 32.61 seconds) and returned APPROVE, no release blockers
within this read-only snapshot scope. First attempt exceeded Windows command-line length;
retry with bounded diff succeeded. Initial Chrome harness used unavailable composite /readyz
on direct main-app fixture; fixed to check authenticated /takeoff HTML. Corrected retest (process 25076, SDK 2.3.0, 23.70 s) passed real Chrome, synthetic PDF, 15 SF field quantity, 14 skill findings, explicit consent, real authenticated API and PG, MCP gateway retrieval, revoke, zero JavaScript errors and failed requests. No private data or production account used.

Not live yet. This is explicitly shared snapshot evidence, not cloud-synchronized editable
projects, raw PDF/geometry retrieval, verified supplier amounts or an owner-approved write-back
proposal workflow. Existing MF-071 broader tool acceptance remains open. No production data used.

Manual browser acceptance is opt-in with MF_TAKEOFF_SHARE_BROWSER_SMOKE=1 and
a disposable loopback PostgreSQL test server. Run tests/test_pg_takeoff_share.py under the
pinned MCP SDK and existing Chrome/Puppeteer environment. Production acceptance requires a
real signed-in pilot owner, explicit synthetic-plan snapshot and refreshed ChatGPT plugin tools.


## MF-095: Source-bound return path (2026-11-25)

Adds optional separate draft consent and an opaque browser project reference plus content fingerprint.
The new MCP tool propose_takeoff_correction requires mf:read and mf:drafts.write, current owner
membership, a live linked OAuth identity, a permitted snapshot, and an unambiguous source record.
Supported kinds: field_quantity (contractor-entered area, length or integer count only) and
review_note (non-executable recommendations about an existing sheet/scope/measurement/material).
Drafts are immutable existing tenant Documents, capped at 10 per snapshot and 8 KiB each;
duplicate contents are idempotent. Revoking through the takeoff page removes the snapshot and
its drafts in one transaction. Business document visibility is disclosed.
Browser fetches current drafts and requires an explicit owner approval button; project reference,
packet fingerprint, source ID, source quantity, assembly, unit and scope link must still match.
The deterministic assembly engine validates/recalculates, the linked scope confirmation is reopened,
and IndexedDB persistence must succeed before showing a successful correction.
Original evidence remains immutable. No direct traced geometry modification, automatic approval,
raw PDF retrieval through MCP, customer communication or firm price override.

Verification: 4 Node draft tests; 51 focused Python tests; 54 tests in pinned MCP 2.3.0 plus real
disposable PG (process 22016, signed HTTP read and signed draft write with missing-scope denial).
Real Chrome synthetic PDF integration process 22804: 15 SF field quantity -> signed MCP proposed
20 SF -> explicit browser owner approval -> IndexedDB 20 SF persisted -> stale reapplication
rejected. Original 15 SF snapshot remained intact; revoked after test; no production data used.
Antigravity independent supplied backend/browser diff review process 27428 returned APPROVE both
(26.12s backend, 22.50s browser); limited to these changes.
Full release regression and production acceptance remain separate gates.

## MF-096: PR16 review corrections
Scope binding rejects missing or stale rendered user/business partition for all snapshot APIs.
Free-text plan fields are stripped before storage (structured IDs, statuses and quantities remain;
not a guarantee of anonymity for deliberately identifying IDs). Unknown severity fails closed.
Generic document deletion cleans dependent drafts under the organization lock. Quota is per
sharing owner. Exact field quantity precision preserved. Late list responses suppressed;
calendar-only fingerprint changes ignored but derived evidence changes still invalidate.
Prior corrective run 25996: 31 focused tests incl real PG, signed SDK and both Chrome journeys
passed, including stale business switch denial; final incremental test and CI separately required.
