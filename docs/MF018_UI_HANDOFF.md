# MF-018 UI Handoff Report — AI Visibility Lab Workflow & Responsive UI

## Summary of Changes
Completed the MF-018 local prototype UI enhancement within the strictly reserved frontend and UI-test file scope (`app/templates/app/modules.html`, `app/templates/app/visibility_lab.html`, `app/static/module_lab.js`, `app/static/module_lab.css`, `tests/test_visibility_ui.py`, and `docs/MF018_UI_HANDOFF.md`). No backend, router, model, migration, or database files were touched.

## Key Capabilities Implemented
1. **Step 01 — GSC Import & Visible Summary**:
   - File upload (`.csv`, `.tsv`), synthetic sample loader, clear import reset, and raw paste support.
   - Displays imported row count, eligible homeowner query count, branded/excluded row counts, total impressions, total clicks, invalid/duplicate row errors, and an expandable table of parsed rows with classification badges.
2. **Step 02 — Traceable Draft Questions, Explicit Approval & Selection Controls**:
   - Preserves all 22 benchmark questions with `Select all benchmark (22)` (`#ml-all`), `Use approved GSC questions` (`#ml-approved-only`), and `Clear selection` (`#ml-none`).
   - Draft GSC proposals display source query lineage (`source_queries` with impressions and average positions) and start unapproved (`approved: false`). Selection shortcuts never auto-approve draft GSC proposals.
3. **Step 03 — Multiple Markets × All Three Search Methods & Exact Request Math**:
   - Replaced the single-market `<select>` with multiple market checkboxes (`input[name="ml-market"]`) plus quick `Select all markets` / `First market only` helpers.
   - Supports all three search methods (`as_written`, `controlled_substitution`, `persona_context`), repetitions (`1–10`), processing chunk size (`1–10`, explicitly separated from total requests), and explicit budget cap (`1–500`) with zero silent 10-search truncation.
   - Live request calculator (`#ml-live-math`) updates `Questions × Markets × Methods × Repetitions = Planned AI requests` and processing chunks in real time.
4. **Step 04 — Readable Evidence Provenance Labels & Safe JSON Draft Import/Export**:
   - Displays readable provenance cards for `Verified provider receipt`, `Captured run (unverified receipt)`, `Historical imported baseline`, and `Quarantined & synthetic cohorts (run-fresh78)` with honest `0 provider calls executed` status and no fake results.
   - Supports explicit JSON plan/draft export (`#ml-export`, `#ml-export-draft`) and safe local JSON draft import (`#ml-import-file`) without browser localStorage. Security hardening by GPT-6: a restored file must carry the expected local-draft type and zero-execution marker; only raw CSV and controls are restored, while untrusted proposals/approvals, claimed GSC totals and imported results are discarded. The owner must re-analyze/reapprove and regenerate the plan; untrusted imported results are never displayed as verified evidence.
5. **Responsive 390px & Desktop Scoped Styling**:
   - Scoped `.ml-*` / `#mf-lab` CSS rules with `@media(max-width:900px)` and `@media(max-width:570px)` breakpoints for clean 390px mobile and desktop layouts.

## Changed Files
- `app/templates/app/modules.html`
- `app/templates/app/visibility_lab.html`
- `app/static/module_lab.js`
- `app/static/module_lab.css`
- `tests/test_visibility_ui.py`
- `docs/MF018_UI_HANDOFF.md`

## Integration review and handoff
- Argon High authored initial UI work and committed it locally as `7224108` after its CLI response had timed out. The original UI contribution remains in Git history.
- GPT-6 took over final MF-018 verification through the board. A restored JSON draft now has strict format and zero-provider-call validation; no imported snapshot totals, client approval flags, question provenance or plan results are promoted as trusted. Users must re-analyze and explicitly reapprove after restoring CSV and configuration controls.
- Independent executable Node.js DOM-harness regression tests exercise the actual JS restore path with forged approval, GSC counts, and provider-call claims. The imported results remain hidden and plan export is disabled until recomputed.
- Verification: `node --check app/static/module_lab.js` succeeded; `python -B -m pytest -q tests/test_visibility_ui.py tests/test_module_lab.py -p no:cacheprovider` produced 21 passed; `python -B scripts/run_checks.py` produced 208 passed, 91 DB-only skipped.
- The UI remains a local research planner. MF-019 provides separate authenticated saved-state APIs, but they are NOT yet wired to these controls. Full browser visual inspection (including 390px viewport), production tenancy, live Gemini, GSC OAuth and public activation remain out of scope.
- Final integration suite: `python -B scripts/run_disposable_pg_audit.py --full-suite` returned `CI_GATE_PASS`, **299 real PostgreSQL-backed tests passed, zero skipped/errors/failures**. Disposable database was used; no production database was touched.
