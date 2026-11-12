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
Agents may edit DIFFERENT file scopes on main concurrently, but MUST claim a user
story on the shared board before editing and stay within its file reservations.
The board's integration gate freezes ALL other source edits while the gate holder
runs the full suite and commits. Always stage only your own changed file paths.
Do not use git add -A, reset, stash or overwrite another agent's work.
The shared board is at:
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\AGENT_BOARD\board.py
Read AGENT_BOARD.md in this repository for exact commands and review procedure.

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
worktree strategy. If another agent has reserved your needed files, choose a different ready story or post a blocker. Never fork.
