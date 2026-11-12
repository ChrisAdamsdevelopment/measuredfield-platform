# MeasuredField — SINGLE AUTHORITATIVE CODEBASE

Owner direction: maintain ONE unified MeasuredField implementation, not competing worktrees.

## One source of truth
The ONLY writable application checkout is:
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\mf_platform

Use Git main as the only active development line. Do not create a Git worktree,
clone, long-lived feature branch, or alternate source directory for MeasuredField.
The prior CLI and Claude branches were consolidated in main commit c350501.
Do not merge already-included commits again. Do not deploy or push without approval.

If ..\CLAUDE_PROD_WORKTREE_20261111 exists, it is a compatibility alias
or archived copy, NOT an independent codebase. Always target canonical mf_platform.

## Coordinate writers
Only ONE coding agent writes to main at a time. Start by checking git status --short
and git log -1 --oneline. If someone is working, stop editing and review only.
Never reset, stash, discard or overwrite their work without explicit review.
Use frequent logical commits, not divergent product strains.

## Claude's unfinished model proposal
Saved outside the active source code at:
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\CONSOLIDATION_CLAUDE_MODELS_WIP_20261111\app_models_unfinished.patch
and app_models_unfinished.py (same folder).

That proposal includes MFA/recovery, audit logs, subscriptions/entitlements,
lead replies/events, AI metering and AI action proposals. It is NOT implemented.
Do not copy it wholesale: first write migration 0004, FORCE RLS policies, API/UI,
authorization, and full tests for a coherent vertical slice. Prefer owner reply
draft/send with verifiable provider evidence as next useful feature.

## Verification and safety
- Run disposable full PG suite for DB/auth/security work:
  python -B scripts/run_disposable_pg_audit.py --full-suite
- Require CI_GATE_PASS, zero skipped tests and zero failed tests.
- Preserve vendor-byte provenance and cross-tenant RLS.
- No live PBC, customer, Render, DNS, Stripe, Gmail or paid actions without approval.
- Never expose credentials or customer data in logs, commits or model prompts.

## Quota-reset continuation
The older Claude Desktop session may resume at 11:30 p.m. Arizona time.
Before any resumed editing, re-read this file and the current git history.
The owner's newer instruction supersedes the previous prompt's separate
worktree strategy. If an unrelated writer is working, stop rather than fork.
