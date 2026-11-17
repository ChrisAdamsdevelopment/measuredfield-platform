# MF-064 — Deterministic PDF Takeoff Geometry Foundation

**2026-11-17, Arizona** · Status: local technical implementation, NOT a professional plan-recognition or production estimating release.

This is the first source-bound measurement engine for the independently reviewed MF-063 evidence contract. It is in the single canonical mf_platform repository. It makes no paid provider calls, customer API requests, purchases, deployment writes or material orders.

## Implemented modules

- app/takeoff/measurement_schema.py — immutable SHA-256/sheet/revision/page/viewport source references, explicit rights, genuine human reviewer event requirements
- app/takeoff/scale_calibration.py — independently checked PDF-point dimension evidence with per-viewport ratios and fail-closed conflict handling
- app/takeoff/geometry.py — deterministic manually selected polygon/hole/line quantities, topology checks, and human review of open paths
- app/takeoff/pdf_ingestion.py — consented bounded PDF bytes, real PDF-parser page metadata, vector/raster/mixed/unknown classification, no embedded document execution
- tests/test_takeoff_geometry_engine.py — offline tests for geometry, provenance, intake, calibration, uncertainty and mutation failures

The above service primitives are NOT a user-facing drawing editor. The coordinates must ultimately come from a real manually edited plan overlay (MF-066). Automatic room recognition does not exist.

## Calibration and geometric evidence

No measurement is eligible for verified takeoff unless there are two distinct consistent dimensions, both human confirmed, from the SAME source SHA-256, sheet, revision, page and viewport. Default relative agreement tolerance is 0.5%. One dimension yields a review_required draft; contradictions throw ScaleConflict. Swapping a revision or viewport throws. No LLM is permitted to approve scale.

PDF points describe the sheet, not physical building inches; actual feet are established through explicit building dimensions on that sheet. A 108-point line spanning 6 feet yields 18 PDF points per building foot. A second human-confirmed 72-point line spanning 4 feet corroborates it.

A 108 x 72-point room measures 24 sqft gross, minus a 36 x 18-point exclusion of 2 sqft = 22 sqft net. The shell perimeter is 20 ft. The inner exclusion boundary is recorded separately (6 ft) and never automatically charged as baseboard. Trade policy and human acceptance govern deductions.

Geometry validation refuses duplicate/zero-length vertices, crossed/touching edges, holes outside or touching the shell, overlapping/nested exclusions, nonfinite coordinates and invalid area. An open path yields OpenBoundary(review_required) with gap size in PDF points and feet when a scale is known: no silent gap healing.

MeasurementReview(human_verified) requires actual reviewer identity and approval reference. New calculations remain review_required by default. This first-pass pure Python topology engine is NOT a comprehensive CAD engine: very large coordinate sets, slivers, curves, scanner distortion and near-collinear floating point require later robust geometry library/property tests before accepting paying work.

## PDF intake and dependencies

inspect_pdf(pdf_bytes, permission=PlanPermission(...)) verifies explicit processing permission, max 24 MiB, max 100 pages, bounded page objects, PDF signature/EOF, and conservative refusal of encryption/embedded-file/JavaScript markers. A real parser backend is required; default is pdfplumber. The parser was not installed in global Python at the initial checkpoint, so absent a reviewed library it raises PdfParserUnavailable instead of guessing.

Page classifications are based on actual parsed lines/rectangles/curves and raster images, per page. Vector, raster, mixed and unknown describe media evidence; they are not automatic rooms, walls, dimensions, or validated pricing. Plan source SHA-256 and sheet/revision/viewport identity are explicit, never inferred by filename.

An isolated, memory/time-capped PDF worker is REQUIRED before external uploads or production. Size and marker checks cannot fully prevent decompression bombs or malicious document behavior. Test on synthetic or otherwise legally authorized plans only. Do not handle unknown private customer drawings as user-granted public assets.

## Tests and evidence levels

Local commands:
- python -B -m pytest -q tests/test_takeoff_geometry_engine.py tests/test_takeoff_fixture_contract.py
- python -B -m ruff check app/takeoff/measurement_schema.py app/takeoff/scale_calibration.py app/takeoff/geometry.py app/takeoff/pdf_ingestion.py tests/test_takeoff_geometry_engine.py

Current tests prove arithmetic and validation on generated geometry and test-only PDF parser stubs. A separate isolated Windows sandbox at D:\\DevCache\\mf-takeoff-pdf-v1 installed reviewed-candidate pdfplumber 0.11.7 and pypdfium2 5.15.2 without changing canonical requirements. A one-off real-parser smoke imported two programmatically generated, legally unencumbered PDFs: one vector PDF returned a vector classification (1 vector object); one Pillow-produced raster PDF returned a raster classification (1 image). The standalone command printed TAKEOFF_REAL_PDF_PARSER_SMOKE_PASS. The temporary test script was removed after the run. This confirms library integration on generated files only; it does NOT prove recognition on real architectural drawings, production package/license acceptance or safe processing of adversarial PDFs. Real legally authorized architectural plan evaluation remains outstanding. There is NO basis to claim professional recognition accuracy, scale detection performance across architects, or customer bid readiness. The independent 25-plan shadow-bid benchmark is in MF-067.

MF-065 handles provider/contractor verified price catalogs, unit conversions, assembly math, waste, formula-linked labor hours, bid export. MF-066 handles beautiful editable PDF drawing overlays and review workflow. MF-068 investigates optional AI/CV proposals after the independent benchmark. MF-071 implements scoped ChatGPT tools after the main plugin and security gates.

## Release and independent review gates

1. Review source, tests, geometry adversarial cases, exact module version/license and failure states.
2. Verify an actual native PDF and raster PDF using a reviewed parser. Mock parser success alone is insufficient.
3. Exercise multi-viewport scale conflicts, scanned/rotated/malformed drawings, user corrections and exact source-coordinate replay.
4. Restrict PDF parsing to a sandboxed process with CPU/memory/time caps before receiving customer uploads.
5. Require MF-067's 25 distinct, legally authorized professional drawings and independent estimator ground truth before any public accuracy claims.

No production access, remote ChatGPT connection, customer plan upload or spend was authorized by this story.
