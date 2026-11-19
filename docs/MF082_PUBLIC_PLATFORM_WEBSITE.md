# MF-082 Public Platform Website and Founder Beta

**2026-11-17 | Marketing-site update; not an OAuth connection.**

## Public website deliverables

- New indexable GET `/platform` with brand, workflow, product groups, readiness states and actionable links. The visitor needs no account to read the page.
- Expanded home hero link, global navigation and `sitemap.xml` to include `/platform` and `/visibility-preview`.
- Real platform scope explained: Website Studio, inquiry workflow, customer/projects/documents, Census-based market intelligence, local search and AI visibility pilots, ChatGPT OAuth/MCP read-only pilot, desktop permissioning, Google providers, memory/evidence loop, and direct beta feedback.
- Public sources are not misrepresented: employer Census 2023 CBP distinct from 2024 nonemployer NES; actual competitor rankings or live Google data are not inferred from aggregates.
- Existing SEO/social preview art, production video/captions, case study and free assessment remain intact. `/platform` has canonical URL, OpenGraph and conservative WebPage JSON-LD. No fictitious testimonials or broad availability guarantees.
- User testing link to the authenticated form `/login?next=/app/feedback`; beta submission and operator triage require successful live deployment of MF-080 plus migration revision 0011.

## Operating boundary

The page presents the whole vision and the current availability of each component. It does NOT assert that private MeasuredField plugin `plugins_6afcba53a45c8191b5c9511a991758aa` is OAuth-connected, that Google/Gmail access is provisioned or that site visitors can give the assistant unrestricted computer access.

Render's app service remains linked to GitHub `main` with autodeploy off. The actual public release requires a reviewed commit, manual deploy, source SHA confirmation, migrations, HTTP smoke and evidence of database recovery risk acceptance.

Do not use a paid Render database upgrade until the owner explicitly approves its fee. The owner stated they will perform the upgrade before 2026-12-13. Synthetic-only founder testing on Free cannot be called durable customer production.

## Verification

- `python -B -m pytest -q tests/test_public_platform_overview.py`
- `python -B -m ruff check app/routers/public.py tests/test_public_platform_overview.py`
- Full disposable PostgreSQL regression required with zero skips/failures
- Review externally, then carefully publish on `main` and deploy manually under a controlled release gate
