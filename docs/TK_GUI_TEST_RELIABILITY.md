# MF-033 — Stable Windows Tk GUI tests

Status: local test-harness reliability fix for MeasuredField's owner Trusted Connections window. No change to production app files, owner permissions, customer folders, browser access, tunnel, or provider calls.

## Problem observed

The existing `tests/test_pairing_owner_ui.py` instantiated and destroyed `tk.Tk()` for each of four native GUI tests, including repeated `gc.collect()`. On the Windows Python 3.12 development machine, later tests intermittently raised TclErrors trying to re-read existing Tcl/Tk scripts (`init.tcl` or `ttk/scrollbar.tcl`). Tests converted these errors to `pytest.skip`, occasionally producing 550 passed / 1 skipped; the project's `scripts/ci_full_check.py` correctly rejects even one skipped test. In contrast, isolated targeted invocations sometimes passed all 26.

This is an observed intermittent initialization symptom; repeated interpreter teardown is a plausible cause, not a definitive diagnosis of the Windows Tcl loader or antivirus.

## Implemented test-only changes

- Module-scoped `tk_root` fixture initializes one withdrawn Tk root per test module, warms up a `ttk.Scrollbar` and keeps the interpreter alive for all four native GUI tests.
- Function-scoped `tk_host` fixture destroys surviving child widgets/popups, explicitly drops Tk `StringVar` / `BooleanVar` references, flushes idle redraw work, and garbage-collects on the GUI thread while Tcl/Tk is still alive. Module teardown also collects any lingering wrappers before disposing of the one shared root.
- On an active Windows GUI, Tk initialization failure triggers a meaningful `pytest.fail` rather than a misleading skip. Headless Unix without a display may still skip, which is intentionally visible to the full CI zero-skips gate.
- Existing synthetic project grants, explicit pairing confirmation, read-only defaults, per-folder audit/details, rejection behavior and revocation assertions remain intact.
- Removed per-test `root.destroy()` and forced `gc.collect()` teardown, and avoided cleanup code that could mask an original failure if panel construction raised an exception.

## Verification

- Before change: full PostgreSQL suite 550 passed and one native Tk test skipped due to Tcl script read error; CI gate failed as intended.
- After the first shared-root change: 26 focused owner UI tests passed six consecutive `-W error` runs. The first full regression run passed 564 with zero skips/errors/failures but emitted a delayed `tkinter.Variable.__del__` unraisable warning during a later threaded test. The fixture now explicitly clears Tk variable references and collects garbage on the main GUI thread while the interpreter is live. After that tightening, 26 focused tests passed eight consecutive `-W error` runs with zero skips/errors/warnings.
- Run `python -B -m pytest -q -W error tests/test_pairing_owner_ui.py` for the focused suite.
- Run `python -B scripts/run_disposable_pg_audit.py --full-suite` for complete real PostgreSQL integration/CI with zero-skip verification and automatic database cleanup.

## Review scope and release gate

Independent Gemini Argon Medium review analyzed repeated-interpreter lifetime behavior and recommended a shared root/interpreter with per-test widget cleanup and explicit Windows failure (review advisory, not an execution result). Request a final independent review of the actual diff, and do not mark this task complete until the full disposable PostgreSQL suite passes after this patch. No public deployment or filesystem access permission change is involved.

## Final release verification — 2026-11-16

- Eight consecutive focused runs: 26/26 passed each run with `-W error`, zero skips or errors.
- Independent Gemini Argon Medium read-only review of final GC/Tk cleanup diff: **APPROVE**; owner/security assertions unchanged.
- Final full disposable PostgreSQL suite: **564 passed, zero skipped, zero errors, zero failures, and no warnings reported**. `CI_GATE_PASS` and `FULL_POSTGRES_SUITE_EXIT=0`; disposable database container stopped and removed.
- Test-only scope preserved; production app, device permissions, authenticators, customer files, API integrations and website deployments unchanged.
