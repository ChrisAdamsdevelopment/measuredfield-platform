# Reuse inventory and provenance

What `mf_platform` took from earlier work, how, and what was deliberately left behind.
Machine-readable hashes: `app/vendor/PROVENANCE.json`. A test
(`tests/test_units.py::test_vendored_components_match_their_recorded_sources`) fails if a
vendored file drifts from its recorded hash. The original PBC and Forge repositories were
read only and not modified.

## Reused

| Component here | Source | Method | Why |
|---|---|---|---|
| `app/vendor/growth_engine.mjs`, `growth_cli.mjs` | PBC `server/growth-intelligence.mjs` (`evaluateGrowth`, `action`), as already extracted to `shared_core/` by GPT-6 | Byte-identical copy of the `shared_core` files | Proven, parity-tested evidence-to-experiment logic. Used on the Insights page with first-party counters only. |
| `app/services/growth.py` | `shared_core/growth_bridge.py` | Adapted: same bounded subprocess and stripped environment; adds a first-party-data wrapper and filters two suggestions whose connectors do not exist yet | Keeps PBC logic out of process and away from secrets. |
| `app/vendor/forge_chunker.py` + `FORGE_LICENSE.txt` | Forge AI `implementation/backend/forge/rag/chunker.py` (MIT) | Byte-identical copy, license retained | Chunks uploaded text documents into tenant-private memory. |
| `app/vendor/hub_privacy_filter.py` | Shared Learning Hub `privacy_filter.py` (Gemini Argon) | Byte-identical copy | PII/secret/identifier redaction, prompt-injection signatures and reconstruction-risk check for the shared-lesson gate; injection check on assessment answers. |
| `app/services/learning.py` | Shared Learning Hub `learning_hub.py`, `security_gateway.py` | Redesigned on the same gate sequence: consent, ownership, privacy, evaluation, human approval, versioning, rollback, revocation, honest deletion receipt | The Hub's logic was sound but bound to SQLite and caller-supplied tenant ids; here identity comes from the session and storage is RLS-protected PostgreSQL. |
| `app/services/sitegen.py` | MeasuredField Studio `app.py` (`validate`, `STYLE`, `WEBSITE`, `write_site`, `RESPONSE_WINDOWS`) | Extracted and generalized into a pure render function; inline script moved to a static file for CSP; more industries, editable headline/intro, service descriptions | The Studio generator and its "only promise a reply time you can keep" rule were the best existing product asset. |
| Structured intake (service + timing) on customer sites | Hub `providers.py` synthetic intake skill, via `studio_bridge.py` | Reimplemented as an optional form block | Proven pattern from the Hub demo. |
| `app/static/site.css` and marketing structure | `public_site/assets/site.css`, `index.html` (GPT-6) | Stylesheet copied unchanged; page rewritten around the two questions; `mf.css` adds new sections | Kept the existing visual identity. |
| Public intake rules: honeypot, consent, bounded payload, origin check, "URL stored never fetched", no PII in logs | `public_site/app.py` | Carried over as behaviour into `routers/public.py` and `routers/site_host.py` | Good rules; reimplemented on the shared backend. |
| Proposal approval evidence (exact revision, explicit approval, "not identity verification" caveat) | PBC `server/approval-check.mjs` | Concept only. PBC detects approval replies in Gmail; here the customer approves from a tokenized link | Same evidence discipline without Google access. |
| Evidence labels (observed / inferred / unknown; "proposed, owner approval required") | PBC growth engine, Hub `funnel_engine.py` | Concept applied across assessment, improvements and insights | Core to not overpromising. |

## Evaluated and not reused

| Source | Decision | Reason |
|---|---|---|
| PBC Google OAuth, sessions, `google-workspace.mjs`, `search-console.mjs` | Not migrated | PBC-specific identity and credentials. A per-customer OAuth connector must be built fresh; the UI shows Search Console as "not connected". |
| PBC `visibility-engine.mjs` | Not migrated | Question bank and domains are PBC-specific and it requires paid model calls. Candidate for a later, neutral connector. |
| PBC `src/estimate.ts`, `pricebook.ts` | Not migrated | Contain PBC labor rates and presets. Proposals here are free-form line items with no shared pricing. |
| PBC `staff-help.mjs` | Not migrated | Prompt text is PBC-internal; its input validation idea (reject contact details and keys) informed the raw-text secret check. |
| PBC customer records, `.env`, `data/` | Never read into this project | Out of bounds. |
| Forge single-user bearer auth | Not reused | Explicitly not a multi-tenant boundary. |
| Forge dynamic module loader | Not reused | Executes arbitrary module code. No plugin or dynamic-code mechanism exists in this platform. |
| Forge `rag/` retriever, embedder, `eval_service.py` | Not migrated | Need a model/embedding provider and a tenant-aware index. Private memory here is keyword search only. |
| Hub `evaluator.py`, `funnel_engine.py`, `providers.py` LLM adapters | Not migrated | Tied to the Hub schema and synthetic metrics. The platform makes no model calls. |
| Studio JSON/JSONL storage, standalone `server.py` export, Studio `http.server` handlers | Replaced | Unauthenticated local storage; superseded by PostgreSQL, sessions and shared hosting. |
| `public_site` SQLite ledger and operator CLI | Replaced | Superseded by `assessments` and the operator console. |

The earlier prototypes are left in place and still pass their own suites (54 checks), so
nothing working was discarded.
