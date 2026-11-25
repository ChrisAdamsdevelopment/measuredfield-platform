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
