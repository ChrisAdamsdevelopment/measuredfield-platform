# MF-024 — Encrypted cloud-to-computer Work relay

Status: production-architecture code on isolated feature branch. NOT DEPLOYED.
Do not advertise as an available customer connection until end-to-end smoke and
independent security review pass. This is a real implementation increment, not
a read-only or draft-only substitute.

## What it does

An OAuth-linked owner/admin must grant mf:read and mf:computer.write. Three
MCP tools are offered: queue_project_file_write, queue_project_directory, and
get_project_work_status. A queued request is not reported as completed.

A confirmed MeasuredField device with access=work retrieves the queued job
using the separate DPAPI-protected device credential over a pinned HTTPS origin.
A second, short-lived desktop pairing grants direct access to designated
readwrite project folders. This separate local pairing is never uploaded to
the cloud. The desktop worker uses BoundSession/AccountRuntimeGuard checks to
save a real UTF-8 file or directory. It returns a SHA-256 work receipt through
the device-only /api/device/v1/work/{job_id}/finish endpoint.

A request is tenant-isolated (PostgreSQL FORCE RLS), linked to its specific
confirmed computer and requester's identity, encrypted with Fernet at rest,
expires after ten minutes, and has a two-minute device claim lease. Retried
requests use one immutable OAuth-owner-scoped request ID and payload hash.
Payload ciphertext is cleared after success, failure or expiry.

Existing files require a baseline hash; blind overwrites are refused. The
worker recognizes matching on-disk contents from a previously completed but
unacknowledged job so a lease retry does not overwrite twice.

## Required operational configuration

- Production server: set MF_DEVICE_WORK_FERNET_KEY to the value saved
  locally at D:\GrowthEngineeringVenture\PRIVATE_AUTH\MeasuredField\
  MEASUREDFIELD_WORK_RELAY_20261119.env (owner-protected file).
  Do not print, commit, or share it. Back up/rotate with an explicit plan:
  losing the key makes pending work unrecoverable.
- Auth0 already defines mf:computer.write; customers still must grant that
  scope. The MCP service must be deployed with the matching tools and OAuth.
- Owner pairs a computer at /app/computers with 'Work in approved projects'
  and confirms its six-digit check number.
- On the connected Windows computer, the owner runs Connected Workspace,
  selects an existing readwrite project root and approves a timed Work-mode
  pairing. Work session grants do not allow shell, delete, deploy, or network
  account changes by implication.
- With a valid session pairing ID, the local worker command is:
  python -m app.desktop_companion.work_poller --pairing-id <local-pairing-id>
  or pass --once to handle one polling cycle. The worker initiates outbound
  HTTPS requests only. There is no public inbound listener or tunnel.
- A queued job must include the opaque local project_id, approved
  relative_path, stable request_id, optional expected_sha256 and content.

## Verification

- python -B scripts/run_disposable_pg_audit.py
  runs migration 0018, FORCE RLS and cross-tenant checks in disposable PG.
- python -B scripts/run_disposable_pg_audit.py --device-work
  runs tenant/work relay tests, including synthetic OAuth owner queue,
  authenticated device claim, literal local disk write and server receipt.
- tests/test_device_work_poller.py uses real temp filesystem writes and tests
  session revocation.
- Never run tests with real customer data, credentials, or live email.

## Remaining release obligations

Independently review the queue and filesystem threat model; prove real
ChatGPT OAuth signup on a non-test account; test Windows installed companion
startup/update and process persistence, installation UX and job cancellation;
test device connection revocation during execution; verify Render key setup,
deployment and rollback, response limits and operational quotas. Add
separately granted safe build/test execution, Git workflow and deployment
action policy, then real connected Gmail/Outlook account sending. Work mode
file writes alone are not the full MeasuredField offering.


## Owner cancellation and privacy controls

Both the OAuth tool cancel_project_work and the owner Computers page can cancel
a QUEUED job. Cancelling erases its encrypted payload and prevents later device
claims. If a job is already leased, cancellation of only that job is refused:
the owner should disconnect the device to revoke work on that machine and
cancel all of its pending/leased jobs. A device can finish only against an
unexpired, matching lease. Revocation stops new claims; a file mutation already
in progress cannot be rolled back automatically.

The server result_sha256 is a claim from the authenticated device, not a
hardware-verified attestation. The desktop worker verifies persisted bytes
locally and returns their digest; credential compromise may still let a
malicious computer report false receipts. Independent signed hardware
attestation is not part of this release.

The worker uses BoundSession and LocalConnect._path for independent local
authorization, safe project confinement, reject traversal and reparse paths,
and SHA compare-and-swap. A stale or unexpectedly unavailable existing file
is not blindly overwritten. A retried directory create must be contained in
the owner-approved project and a previously-created file is matched by digest.

## Master-key rotation procedure

Each business's ciphertext uses a distinct derived key: HKDF-SHA256 over the
32-byte primary Fernet master with an organization-specific context. One
compromised master can still decrypt every business's queued jobs; a KMS-based
per-tenant envelope key is a future improvement. Keys must never appear in
source code, logs, device response bodies or database rows.

1. Save the new Fernet master in a restricted owner-only local D: drive file;
   preserve a protected copy of the previous active master. Do not overwrite
   or publicly expose the original key.
2. Prefer draining the queue and confirming no queued/leased jobs remain.
   If draining is impossible, deploy the new master as
   MF_DEVICE_WORK_FERNET_KEY and place the previous master in
   MF_DEVICE_WORK_FERNET_PREVIOUS_KEYS (semicolon-separated, maximum 3 old
   keys). The first key encrypts new jobs, all configured keys can decrypt
   old pending jobs. Deploy these environment updates atomically.
3. Maintain the previous master for at least the full 10-minute job TTL plus
   lease/processing/drain allowance; do not remove it while old ciphertext
   remains pending.
4. Verify new enqueues and old outstanding jobs can be claimed and finished,
   then confirm all old jobs have succeeded, failed, expired or been cancelled.
5. Remove previous keys only after the old queue is empty, retain a protected
   recovery record for incident investigation, and audit the rotation.
6. If a key is suspected compromised, stop new work, revoke affected sessions,
   cancel queued work, investigate, rotate the master, and review access logs.

The local key handoff is
D:\GrowthEngineeringVenture\PRIVATE_AUTH\MeasuredField\MEASUREDFIELD_WORK_RELAY_20261119.env.
It has NOT been added to Render as part of these branch-only changes.
