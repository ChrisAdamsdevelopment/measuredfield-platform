# Independent engineering findings — Claude, 2026-11-11

Where I disagree with the current direction, what I think was overlooked, and which
assumptions deserve more scrutiny. Ordered by how much I think each matters.

## 1. The process is riskier than the code right now

Three AI agents edited the same directory at the same time with **no version control**.
During this session `deps.py`, `config.py`, `models.py`, `workspace.py`, the CI workflow and
several templates changed under me; I noticed only from file timestamps. Most of those
changes were good. One was not: migration 0003 would have failed on the first staging
upgrade with data in it (below). Nobody would have known which agent introduced it or when.

Do this before anything else: `git init` in `mf_platform`, commit, and require every agent
to work on a branch with CI passing. Review by a second model is only meaningful against a
diff. The line-numbered text bundles being prepared for review are a workaround for the
missing history.

## 2. Tests that only run on empty databases hide a whole class of defect

Migration 0003 (referential integrity) passed its tests and failed on a populated database:
PostgreSQL validates a new foreign key as the table owner, and under `FORCE ROW LEVEL
SECURITY` the owner sees no rows. Fixed, with a regression test that upgrades a database
containing tenant data. **Every future migration that touches a tenant table needs that
test**, and the same interaction will bite `pg_dump`/restore, ad-hoc owner queries, and any
analytics job: under FORCE RLS the owner reads nothing without a tenant context. Decide now
how operators will legitimately run cross-tenant maintenance (a dedicated, audited
`BYPASSRLS` role used only by migrations and backups is the usual answer) instead of
discovering it during an incident.

## 3. "Ignore all proxy headers" was safe and wrong

The earlier hardening made the app ignore `X-Forwarded-For` entirely. That stops spoofing,
but behind Render every visitor then shares one address, so the sign-in limit of 20 attempts
per 15 minutes applies to the whole customer base at once. Twenty bad passwords from anyone
lock everyone out. I added `MF_TRUSTED_PROXY_HOPS` (default 0, unchanged behaviour), which
uses only the entry written by our own outermost proxy. The hop count must be measured on
the real deployment; the runbook says how. Argon's "take the rightmost" and GPT-6's "trust
none" were each half of the right answer.

## 4. The reused privacy filter has an ordering defect

`privacy_filter.py` (Shared Learning Hub) redacts phone numbers before API keys. A key such
as `sk-abcdef1234567890` becomes `sk-abcdef[REDACTED_PHONE]`: the prefix survives and the
filter reports **no secret**, so a gate keyed on its findings passes. I check the raw text
first and left the vendored file byte-identical; the Hub copy should be fixed at source
(run secret patterns first, or collect findings on the original text). More broadly,
regex redaction is a risk reducer, not anonymization. The human review step is the control
that matters, and `assess_k_anonymity_risk` is a keyword list, not k-anonymity; its name
overstates it.

## 5. Strategy: the shared-learning advantage is unproven and should not drive the roadmap

The thesis is that the platform gets better as it solves more businesses' problems. The
mechanism now exists and is safe (consent, redaction, human approval, revocation). But with
zero customers there is nothing to learn from, the candidate lessons are free text written
by busy owners, and a human must review each one. I would keep the gate, ship it dark, and
spend the effort on the thing owners will pay for in month one: inquiries that do not get
lost. Revisit shared learning when there are 20 or more active workspaces and real
observations to mine.

## 6. Strategy: three surfaces is right; one more boundary is missing

Serving customer sites from the same process is efficient and I kept it. But customer sites
should live on a **different registrable domain** from the app (the way hosted-content
platforms separate user content), decided before the domain is bought. It costs nothing now
and is painful to change once customers have links in the wild. `MF_SITES_BASE_DOMAIN`
already allows it.

## 7. Pricing and billing assumptions

* $99 per month and $199 one time are hypotheses carried forward from earlier notes. They
  are configurable (`MF_OFFERS_JSON`) and labelled introductory, but nothing validates them.
  The unit-economics model in the research folder should be rerun against the real cost of
  the smallest Render footprint (web + worker + database) per customer count.
* The monthly offer cannot be sold yet: checkout would create a subscription that nothing
  manages afterwards. Sell the one-time setup or invoice manually until the lifecycle exists.
* Free consultations create real work for a human. There is no calendar or capacity limit.

## 8. "AI-assisted" is currently rules, and that is fine, but say so consistently

No model is called anywhere. The assessment and insights are deterministic and labelled as
such in the product. That is the right first version: predictable, free to run, impossible
to prompt-inject. The marketing copy avoids claiming AI. Keep it that way until a model
demonstrably beats the rules on real assessments, and when it does, put it behind the same
"proposed, needs your approval" contract with a hard monthly budget. The keyword rules will
misread plenty of real answers (negation, sarcasm, industries I did not anticipate); every
assessment should be looked at by a person for the first hundred customers, and the
`needs_human_review` flag is there for that.

## 9. The owner still has to answer inquiries somewhere else

The inquiry desk records status, assignment and follow-ups, but replying happens in the
owner's own email or phone, and the "first response" metric is therefore self-reported
(the product says so). The response-time figures a collaborator added to Insights inherit
that limit. Sending replies from the workspace (with the owner's approval and a real email
provider) is the first feature that would make the measurements trustworthy, and it is the
natural next build after email activation.

## 10. Smaller points

* A Node subprocess per Insights page view is acceptable now; port `evaluateGrowth` to
  Python with a parity test when traffic exists. A collaborator's native fallback is a start
  but is explicitly not parity-tested.
* Prospect assessments have no retention period.
* Operator actions need their own audit log before a second operator exists.
* `managed authentication` was the stated preference and is not what was built. Passwords
  with Argon2id and database sessions are sound, but MFA, device management and account
  recovery are exactly what an identity provider is for. I kept the seam narrow so it can be
  swapped.
* The collaborator's CI workflow replaced mine and dropped three guards worth restoring:
  failing when the PostgreSQL suites are skipped, `alembic check` for model/migration drift,
  and installing Node so the growth-bridge test runs.
* Legacy prototypes (`13_`, `14_`, `public_site`) are now superseded. Freeze them, keep them
  for provenance, and stop running their local servers.
