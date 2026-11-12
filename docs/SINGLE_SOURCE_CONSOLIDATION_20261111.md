# One-source MeasuredField consolidation — November 11, 2026

## Owner decision
ONE canonical checkout on Git main, serialized writing by Claude, GPT-6 and
Gemini Argon. No competing live MeasuredField versions.

## Consolidated commits
- 102e303: initial verified local baseline
- ff78ddf: CI gate with real PostgreSQL, Node checks and vendored byte preservation
- c350501: Claude's correction to original Forge/PBC vendored bytes

Main fast-forwarded to c350501, inheriting both improvements.
Main passed disposable PostgreSQL full suite: 112 passed, 0 failed, 0 skipped,
CI_GATE_PASS; disposable container removed after testing.

## Preserved unfinished Claude schema
Claude's standalone app/models.py proposal, last modified around 7:26 p.m.,
contains candidate schemas for MFA, auth-token issuance, operator audit trails,
subscriptions/entitlements, outbound lead replies, lead events, AI metering and
approval-gated AI actions. This is a design draft, not a working capability.
Its app_models_unfinished.py and Git patch are preserved in:
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\CONSOLIDATION_CLAUDE_MODELS_WIP_20261111

Next integrate it into main only as verified vertical slices with Alembic 0004,
RLS for all added tenant tables, authorization and provider-mock tests.

## Coordination
Agents start at:
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\mf_platform
One writer at a time. Check CLAUDE.md/AGENTS.md, git status, recent commits.
Do not create another worktree or switch to a separate long-lived branch.
No public deployment or provider activation until independently authorized.

## Automatic Claude continuation warning
Claude Desktop recorded quota exhaustion at 7:26 p.m., resetting at 11:30 p.m.
Arizona time. No matching Windows scheduled task was found. Claude's own UI
may auto-continue. We cannot prove that its already-running session will read
new instructions; its continuation must be reviewed for stale worktree commands.
