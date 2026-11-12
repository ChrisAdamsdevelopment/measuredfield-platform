# MeasuredField ChatGPT plugin — MF-012 implementation boundary (2026-11-12)

## User goal
A business owner connects their MeasuredField account in ChatGPT, then can retrieve their own businesses, verified inquiry records, and first-party operational insights. This is a plugin backed by authenticated remote MCP, not an embedded MeasuredField chatbot or a separate product.

## Canonical source
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\mf_platform

The official MCP Python/Node SDKs and the offline starter are installed separately at:
D:\GrowthEngineeringVenture\15_INTEGRATION_WORKBENCH_20261111\CHATGPT_CONNECTOR_WORKBENCH
See SDK_SETUP_20261112.md there. No SDK code is copied into the canonical app unless claimed and tested.

## First-party adapter
app/mcp_connector/gateway.py provides a SQLAlchemy / PostgreSQL FORCE-RLS read-only gateway:
- A trusted resource-server boundary must construct VerifiedOAuthIdentity *only after* verifying OAuth token signature, issuer, resource audience, expiration, scopes and revocation.
- The adapter compares expected issuer and resource audience, enforces mf:read, and requires a trusted server-side (issuer, sub) -> internal User.id resolver. Unconfigured resolver denies all users.
- Every tool call re-checks live user enabled status and the requested business membership/role before setting the RLS tenant context. Deleted or deletion-pending businesses are rejected.
- Business lists are scoped to the linked user, detail reads require a matching tenant ID. Unlinked or unauthorized requests never reveal whether a foreign business or inquiry exists.
- Only first-party records are returned. No invented Google rank, conversion lift or attributed revenue. Current adapter has no external provider calls and performs no writes or sends.

## Deliberate release blockers
1. **No live OAuth resource-server wiring or identity provider.** Do not let model input or raw user ID impersonate VerifiedOAuthIdentity. Implement official MCP v2 server, verified token middleware, protected-resource metadata, issuer/sub mapping with revocation, and OAuth flow under approved scopes.
2. **No external HTTPS /mcp endpoint, plugin registration or ChatGPT connection.** The SDK starter is offline only and defaults to UnconfiguredGateway.
3. Tenant gateway needs real PostgreSQL A/B tests, revoked membership/disabled owner checks and peer review before merge of broader feature work.
4. Production needs edge rate limiting, audit trails, OAuth session/consent UX, privacy notices, security review and explicit deployment authorization.
5. Read-only `preview_reply` is allowed only as unsaved text with zero external action. Sending/publishing/billing belongs to MF-013 after explicit owner approval and server-side action confirmation.

## Tests and acceptance
`python -B -m pytest -q tests/test_mcp_connector.py` performs unit tests but skips actual DB tests unless MF_TEST_PG_ADMIN_URL is set.
`python -B scripts/run_disposable_pg_audit.py --full-suite` provisions an isolated PostgreSQL 16 with separate schema-owner/runtime roles and exercises forced tenant RLS. Require CI_GATE_PASS and zero skipped.
Do not interpret a passing adapter test as authentication of a ChatGPT client.

## Next integration slice
After independent review of the adapter, implement a durable verified issuer/sub subject mapping, wire official `mcp.server.mcpserver.MCPServer` and `streamable_http_app` behind OAuth 2.1, and test with the real restricted runtime role and synthetic two-tenant accounts. This should happen under a new board reservation if it touches app/config.py, migrations, or dependencies.

## Official product documentation
- https://developers.openai.com/plugins/quickstart
- https://developers.openai.com/plugins/build/mcp-server
- https://developers.openai.com/plugins/build/auth
- https://developers.openai.com/plugins/build/app-quickstart
- https://github.com/modelcontextprotocol/python-sdk
