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

## MF-097: OAuth reconnect discovery correction (2026-11-25)

Real connected ChatGPT tools now read the synthetic 15 SF snapshot, including
draft consent, but attempting the draft triggered a repeated reconnect.
Production RFC 9728 metadata advertised only mf:read, while the new tool
requires mf:drafts.write. SDK 2.3.0 uses required_scopes for both purposes.
Replace only the public metadata route with the SDK's supported-scopes route
factory; required transport scope remains mf:read. Per-tool signed identity,
additional scopes, current membership, snapshot consent and owner approval
are unchanged. Advertise the four existing implemented scopes, not a blanket
write grant. 57 SDK tests and 52 focused real PG tests passed; signed protocol
challenge checked for exact additional scope and error. Independent Argon Low
review PID16840 APPROVE. Actual Auth0 permission issuance and live reconnect
must still be tested; the metadata mismatch is a verified defect, not proof
of the only cause of the reconnect loop.

## MF-098 adaptive remodel product choices

Website stage 3 now prioritizes existing measured material lines in a six-at-a-time queue:
all, attention, supplier quotes, selections/upgrades. No project-specific item catalog,
no automatic premium markup, no private case amounts encoded. Works over any existing
assembly, including kitchen, bath, interior, garage conversion and deck.
A saved product level/specification/supplier is browser-local project data and survives
backup serialization. Explicit selection change clears its old price/cost classification,
not geometry or other rows, and requires installation-impact and labor review. Linked
quotes are protected. Specific selections suppress generic planning references; contractor
can source actual prices. Installation review is tied to current line quantity basis.
This is not a complete product dependency engine: no automatic change to substrate,
labor or other quantities, no supplier request/receipt UI yet, and no new MCP tool or
shared editable project. Private selection text is not exported through the MCP snapshot.
Tests: 35 Node tests pass; existing synthetic-owner real Chrome smoke PID29584 pass;
new real Chrome kitchen queue/custom selection/persistence/mobile PID20788 10 passed,
including ADAPTIVE_REMODEL_SELECTION_REAL_CHROME_PASS. Initial browser test failed only
on the local Puppeteer import path; corrected and rerun. Production deployment separate.

## MF-099: restore actual existing pricing and quote workflow
Canonical app now exports all 189 CC BY research references (188 priced, one unresolved), with original source units and ranges, source and attribution. Database SHA256 793641cf6a65bea4a5110a5a7dcdb7faa1408de26299afba6cac2fc48daa31a7. No restricted retailer records included. Explicit reviewed conversion and cost coverage required before applying a reference allowance. Source quantity is entered in original unit; low/typical/high are price points, not certified grades. Existing quote engine now exposed for single-line request preparation, external request recording, offer receipt, review, approval, expiry, rejection and archive. Old price evidence retained after explicit replacement. Missing or changed quote basis and legacy multi-line quote cannot enter guided estimate. Multi-line replacement currently requires new individual quotes; archive retains original. Browser-local save; no supplier communication is sent. Existing 13 automatic mapping references unchanged.
Evidence: PID2596 13 Node tests; PID12980 14 browser-enabled tests passed, PID29124 corrected browser 4 passed including replacement consent and persistence. Independent review PID12252 requested changes, corrected; PID29408 APPROVE. Remaining: commercial reuse review for 5964 supplier candidates, 45 specialty refs; detailed labor reference search and planner, full multi-line procurement, cloud editable project and MCP pricing proposals, independent real-plan comparison. No new paid acquisition.
