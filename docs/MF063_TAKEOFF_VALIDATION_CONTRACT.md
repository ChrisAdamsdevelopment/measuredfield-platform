# MF-063 — Real-plan takeoff evidence contract and independent benchmark design

**Date:** 2026-11-17 Arizona · **Story:** MF-063 · **Status:** specification + synthetic contract tests only; no production engine, customer plan upload, automatic measurement, credential or paid API call.

## Purpose

Before implementing a takeoff engine, specify precisely what a **measurement** means, how it is derived from a named document revision/viewport, how human verification works, and what evidence would count as professional accuracy. This is not a benchmark claim or release certificate.

**System boundary:** MeasuredField's single canonical `mf_platform` repo and PostgreSQL tenant isolation. MF-064 creates deterministic geometry; MF-065 creates formula-linked costs; MF-066 creates professional PDF canvas; MF-067 performs a 25-real-plan blinded expert evaluation; only then may MF-068 propose optional AI/CV geometry. This story reserves only this document, the synthetic README specimen and offline contract tests. No new takeoff service or customer data.

## Evidence provenance classes

| Class | Meaning | Allowed uses |
|---|---|---|
| `synthetic` | Created specifically to test deterministic rules | Smoke/roundtrip, never customer benchmark or professional accuracy |
| `licensed_real` | Legally obtained third-party plan with documented allowed processing and machine-learning terms | Blind benchmark only with rights/consent and professional ground truth |
| `owner_authorized_real` | Plan the owner has rights and actual consent to process | Controlled pilot, blinded benchmark if independently reviewed, never blanket redistribution |
| unknown/restricted | Rights uncertain, customer permission absent or drawings contain unredacted personal/secure details | **Reject from tests and public repository**; no third-party model upload |

A real fixture requires document rights basis, explicit owner/customer consent, source SHA-256, a stable restricted-storage locator, redaction/privacy decision and any external inference permission. Source metadata includes sheet number, revision, page index, viewport, document kind, orientation, effective crop/rotation transform, source hash, and the expert ground-truth reference. These are NOT optional to call a result professionally reviewed. Never put licensed drawings, contact addresses or customer credentials into Git test fixtures without explicit grant.

Example sample at `tests/fixtures/takeoff/README.md` is only an embedded JSON fixture. Its placeholder hash is intentionally not a real content hash, its reviewer reference is null, and prices are unverified. The corresponding `tests/test_takeoff_fixture_contract.py` checks the claims within that sample, not computer-vision recognition. Failures distinguish synthetic consistency from withheld external proof.

## Coordinate and scale model

Use page coordinates in PDF **points** (1 point = 1/72 inch) after controlled CropBox/MediaBox and page rotation interpretation. Each `sheet_id + revision_id + page_index + viewport_id` has an independent calibration record with coordinate-to-real transform and evidence:

1. Dimension-string or measured calibration endpoints (e.g. 108 PDF points spanning a confirmed 6 real feet → 18 points/ft).
2. Exact scale notation + user-observed cross-check to a printed dimension or measurable known feature.
3. Human-confirmed revision and viewport scope; an alternate scale region must never silently reuse another viewport's transform.
4. Any conflicting dimensions, ambiguous rotated/scanned scale, cropped title block, image resizing, skew or uncalibrated source is `review_required` and **cannot contribute approved bid quantities** until fixed.

If PDF streams have paths, separate strokes/text/dimension leaders from material geometry. For scans record DPI, rotation, deskew parameters, distortions and vertex review, never infer perfect scale from raster DPI alone. Keep source coordinates unchanged and version all derived polygon edits.

## Geometry contract and trade policy

A quantity row points to unique `measurement_id`, page and viewport reference, **actual** outer polygon/line/count geometry, unit, trade, revision, rule version, exceptions and approval event. Deterministic geometry: polygon gross = shoelace area / scale²; net = gross - explicit approved hole/deductions; lengths/perimeters are segment Euclidean sums / scale. Round only at report/export boundary; use unit conversion and numeric tolerance tests. Reject self-crossing polygons, zero-length segments, holes outside outer boundaries, duplicate measurement IDs and unclosed/incorrect topology rather than assigning plausible quantities. (Production geometric validators belong to MF-064.)

**Deductions are trade-specific and must be human-reviewed.** A kitchen island may affect finished flooring area depending on installation plan; it must not automatically generate baseboard around the inside exclusion. Door thresholds, openings, cabinetry, wall areas, tile niches and paint openings each need independent trade policy and documented assumption. Never merge floor area with wall face area or gross wall perimeter with baseboard length.

An unresolved 5.4-pt/3.6-inch open-wall gap in the Argon synthetic drawing was **flagged**; no blanket silent gap healing can be treated as verified geometry. Proposals show endpoints and measured physical gap, class and confidence; an estimator accepts/rejects. Source geometry and correction history remain inspectable.

## Drawing revision, evidence and symbol-count ground truth

- Revision is part of every measurement key; a superseded source measurement cannot silently contaminate a new bid. Compare revised boundaries and manually re-confirm changes.
- Ground-truth symbols are exact or toleranced bounding boxes/points with `symbol_id`, `class`, `sheet/revision/viewport`, and an independent labeler. Distinguish decoys and drafting legends.
- Evaluate symbol predictions by **one-to-one, class-aware spatial matching** within a pre-agreed IoU or centroid threshold. Total count equality alone does not establish precision/recall. False positives and false negatives must be recorded separately.
- Record detection status as `measured_manual`, `algorithm_proposed`, `human_corrected`, `professionally_reviewed`, `rejected`, or `unknown` in the future domain model. A prompt result is not approval.
- Corrections must be attributed to reviewer identity, timestamp and immutable geometry/price version. The signer for estimate release must be a real authorized estimator. Not a simulation.

## Original Argon evidence and caveats

Argon R1/R2 generated synthetic plans and an XLSX. Independent rerun of the supplied 810-line Python produced **0.00%** area/perimeter error only on six self-generated recognized polygons, 12 synthetic symbol counts, raster living-area **259.8 vs 270 sqft (3.78% difference)**, a synthetic open-wall gap, and a 16-line illustrative corrected estimate at **$8,161.53 direct / $11,936.24 bid**. Those are not actual plan interpretation results, customer quotes or financial ROI. R1 and R2 cite other raster percentages on different test fixtures/versions, not an independent accuracy series.

**Known defects to block until addressed:**
- Hard-coded page coordinates for labels, known-position dimension calibration, unknown polygons possibly scoring against their own measured values (circular ground truth).
- Existing symbol count scoring lacks independent one-to-one spatial matches.
- XLSX labor hours P5:P20 are constants, not formulas linked to changed quantity. New MF-065 must recompute labor production hours when quantity changes, along with waste, packaging, labor dollars, tax, overhead, contingency and markup/gross margin.
- `APPROVED` or `Contractor Verified Catalog` labels lack actual signature, price source, date and audit. Sample rates must be marked synthetic.
- Interior-hole area can be separately counted in a geometry summary; must never silently be included in finished floor material quantities unless an explicit trade rule requires it.

## Pre-registered future 25-plan shadow benchmark (MF-067)

**Candidate sampling plan:** at least 25 **distinct real, legally authorized historical plan sets**, each withheld from calibration/model tuning. Proposed stratification for feasibility assessment: 8 clean native vector projects, 8 real scanned/raster projects of varying quality, and 9 difficult/multi-viewport/mixed-scale/revision or drafting-style projects. This grouping is provisional and can be revised **before** any benchmark runs; report any overlapping conditions rather than claiming independent samples if a sheet belongs to multiple categories.

**Human expert baseline:** two experienced reviewers independently annotate source sheets and quantities by trade, reconcile disputed boundaries, sign a gold-standard set, and record elapsed manual estimate time under a consistent scope. Blind MeasuredField operator gets the same permitted documents without accessing reference labels. Keep algorithm iterations/tuning separate from locked evaluation sets.

For each project log: file hash and right-to-use record; revision; scale and any conflicts; class/trade; ground-truth individual polygon/line/count annotations; model/manual proposal; human corrections; initial vs final quantities; source references; provenance and timestamps; correction count; unreviewed/abstain rate; time-to-approved-estimate; unit-cost formula differences; and any unsafe automated estimate publish attempt.

**Metric definitions:**
- Area/length relative error = `abs(measured - ground_truth) / ground_truth`, reported by trade, source type and distribution (median/p90/worst); near-zero ground truth uses absolute tolerance and reports denominator handling.
- Object detection precision = TP/(TP+FP), recall = TP/(TP+FN), F1 harmonic mean; match by spatial coordinates and class, *never* by total counts alone.
- Scale-conflict sensitivity = discovered true incompatible viewport/dimension conflicts / all independently labeled conflicts, with **no silent wrong-scale pass** as the desired safety gate.
- Cost formula reconstruction drift = independently recomputed quantities × reviewed unit rates, packaging, labor, margin vs exported totals. **$0 drift** is a release target for matching input versions, not a property of arbitrary supplier quotes.
- Time gain = 1 - (assisted elapsed time / manual reference elapsed time), reported as paired distribution and excluding precomputed calibration labor; **≥50% median improvement** is a product aspiration, not a verified result.
- Miss/abstain/uncertainty and reviewer burden are as important as mean area error. High-risk unreviewed geometry must fail closed.

**Provisional phase-gate hypotheses** (NOT claimed achieved): vector area relative error ≤0.25% and human-corrected raster area ≤1.50% against independently annotated reference; 100% detected scale/viewpoint conflict cases in the locked test; formula-total reconstruction drift $0 for identical inputs; ≥50% time reduction across 25 complete real projects. These thresholds are starting hypotheses; professional estimators must confirm that they match appropriate trade tolerances and real-world complexity **before** accepting paid use.

A failure, absent document rights, missing expert reviewer, unusable plan, ambiguous scale or unknown price remains failure/pending—not zero error or a valid completed job. Public professional-grade claims require independent sign-off, release history and correct customer legal terms.

## Engineering and review gates

1. **MF-063:** immutable fixture contract, source rights and independent benchmark design; synthetic unit tests and separate reviewer approval. **No real plans imported here.**
2. **MF-064:** vector/raster document parsing, calibration, robust 2D geometry and uncertainty states; source-linked reproducible arithmetic.
3. **MF-065:** real owner-driven assembly/rate catalog, unit conversions, dated prices, formula-linked labor and audit-safe CSV/XLSX export.
4. **MF-066:** premium desktop drawing workbench (pdf.js/SVG/canvas) with zoom/pan, editable polygons, explicit reviewer approval, and estimate-row → source-drawing traceability. Tablet/phone for field review; no claim of tiny-screen CAD precision.
5. **MF-067:** 25-real-plan blind assessment with signed reviewers; correct threshold application by scenario; block production if not met.
6. **MF-068:** optional CV/ONNX suggestions and legitimate Kreo licensing/API comparison after baseline; proposals only, human-reviewed.
7. **MF-071:** tenant-scoped MCP read-only project/measurement evidence and queued write proposals behind account authorization, no automatic estimate send, purchases or raw desktop file access.

**Do not** copy proprietary plan content, source model weights under non-commercial terms or production secrets into the repository. No endpoint, tunnel, paid supplier quote or provider API call is authorized by this document. MF-056's architectural gate was independently approved by Claude on 2026-11-17; this MF-063 requires a separate non-author review.

## Locally verifiable synthetic evidence

`python -B -m pytest -q tests/test_takeoff_fixture_contract.py`

`python -B -m ruff check tests/test_takeoff_fixture_contract.py`

Expected contracts: synthetic 24 sq ft gross - 2 sq ft deduction = 22 sq ft net, 20-ft outer perimeter, 6-ft hole perimeter. Mutation tests reject inconsistent scale, sheet references, self-made professional approval, count-total-only matching, fabricated supplier verification and unlicensed real fixtures. This is a **schema/contract test**, not a substitute for geometrical polygon validity, real PDF ingestion or the 25-plan benchmark.
