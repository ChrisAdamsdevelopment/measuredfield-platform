# Gemini Argon independent review — verified P0 coding task (2026-11-11)

## Source
User provided Argon's review of the bundled workbench. The review was TRUNCATED before `mf_platform/app/security.py`, routers, services, and migration 0002. Read actual local files before fixing. Full source is in this project; segmented line-numbered review bundles are at D:\mf_platform_only_part_01_of_06.txt through D:\mf_platform_only_part_06_of_06.txt.

## Findings to independently verify and address
1. `app/deps.py::client_key` and `app/routers/site_host.py::_client` read the leftmost `X-Forwarded-For` entry if `MF_TRUST_PROXY_HEADERS=true`. This might permit rate-limit evasion. Correct using a verified trusted-proxy chain: do NOT blindly trust any header or assume the rightmost is always safe. If proper proxy provenance unavailable, fail closed or use validated trusted configuration.
2. `app/main.py::HostRouter._health('/readyz')` exposes Alembic schema revision and invokes DB isolation-check queries on every unauthenticated request. Return minimal external status and bound resource usage. Preserve fast healthz, verify DB readiness, cache expensive checks or do only at startup.
3. `app/deps.py::render/back` trust raw `?ok=` and `?err=` values for flash messages. Prevent an attacker from using the real app's UI for URL-based phishing. Preserve user feedback without untrusted URL text.
4. Evaluate `app/main.py` origin/host normalization and any similar issues with evidence before patching.

## Argon findings that were NOT supported as stated
- `app/migrations/versions/0002_row_level_security.py` already uses `NULLIF(current_setting('app.org_id', true), '')::uuid`; empty GUC does not cause the alleged UUID cast problem in that function.
- `app/routers/site_host.py` uses explicit `with session_scope()` for its DB work, not the main `get_db` dependency. Don't "fix" a non-existent connection leak or introduce new session lifecycle bugs.
- Argon recommended simply taking the XFF rightmost IP. That is not a universally safe fix; configure and verify trust boundary.

## Coding/verification instructions
- Work ONLY under this `mf_platform` directory; other D: projects are read-only source reference.
- This is a bounded implementation iteration, not a roadmap. Patch confirmed issues with smallest reliable changes, add focused tests; no deployment, credentials, paid services, network scanning, or customer data.
- Run Python import/parse and focused tests; document exact commands, results, and any blocked integration tests.
- Do not pretend current tests demonstrate Postgres RLS or full production readiness. Existing `tests/` may be empty.
- Leave clear report `docs/ARGON_P0_HARDENING_REPORT.md` with changes, limitations, and next steps.
