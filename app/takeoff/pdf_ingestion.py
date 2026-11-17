"""MF-064: bounded, permissioned PDF intake with honest vector/raster labels.

This service reads authorized bytes in memory. It does NOT infer room geometry.
A real parser is mandatory; missing dependencies fail closed rather than giving
an untrusted lexical guess about drawing contents.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
from typing import Any, Callable, Literal

from .measurement_schema import DrawingRef, PlanPermission

MAX_PDF_BYTES = 24 * 1024 * 1024
MAX_PAGES = 100
MAX_PAGE_OBJECTS = 200_000
PageKind = Literal["vector", "raster", "mixed", "unknown"]


class PdfIntakeError(ValueError):
    """Unsafe, unreadable, oversized or unsupported PDF input."""


class PdfParserUnavailable(PdfIntakeError):
    """A documented, trusted real PDF parser must be installed first."""


@dataclass(frozen=True)
class PageInspection:
    index: int
    width_pdf_pt: float
    height_pdf_pt: float
    kind: PageKind
    vectors: int
    raster_images: int
    text_chars: int


@dataclass(frozen=True)
class PdfInspection:
    source_sha256: str
    size_bytes: int
    pages: tuple[PageInspection, ...]
    rights: PlanPermission

    def drawing_ref(
        self,
        *,
        sheet_id: str,
        revision_id: str,
        page_index: int,
        viewport_id: str,
    ) -> DrawingRef:
        if type(page_index) is not int or not 0 <= page_index < len(self.pages):
            raise ValueError("Unknown PDF page number")
        return DrawingRef(self.source_sha256, sheet_id, revision_id, page_index, viewport_id)


def _pdfplumber_factory(stream: BytesIO):
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PdfParserUnavailable(
            "Install reviewed pdfplumber in the takeoff environment; "
            "PDF geometry cannot be inspected without a real parser."
        ) from exc
    return pdfplumber.open(stream)


def _bounded_length(value: Any, field: str) -> int:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_PAGE_OBJECTS:
        raise PdfIntakeError("Unreasonable number of " + field + " PDF objects")
    return len(value)


def inspect_pdf(
    payload: bytes,
    *,
    permission: PlanPermission,
    parser_factory: Callable[[BytesIO], Any] | None = None,
) -> PdfInspection:
    """Inspect authorized plan bytes without running JavaScript or storing files.

    PDF object counts are provided by an actual PDF parser. A supplied factory
    is intended for isolated tests; normal callers use pdfplumber only.
    Page kind is a descriptive classification, never a measurement approval.
    """
    if not isinstance(permission, PlanPermission):
        raise PdfIntakeError("Explicit document rights/consent required")
    if not isinstance(payload, bytes) or not 20 <= len(payload) <= MAX_PDF_BYTES:
        raise PdfIntakeError("PDF is empty, malformed or exceeds size limits")
    if not payload[:16].lstrip().startswith(b"%PDF-"):
        raise PdfIntakeError("PDF signature is missing")
    if b"%%EOF" not in payload[-2048:]:
        raise PdfIntakeError("PDF end marker is missing")
    # This is intentionally conservative defense-in-depth. A PDF parser and
    # isolated renderer remain required for production-safe untrusted plans.
    forbidden = (b"/Encrypt", b"/EmbeddedFile", b"/JavaScript", b"/JS ", b"/Launch")
    if any(marker in payload for marker in forbidden):
        raise PdfIntakeError("Encrypted or active-content PDF requires manual review")
    factory = parser_factory if parser_factory is not None else _pdfplumber_factory
    results: list[PageInspection] = []
    try:
        with factory(BytesIO(payload)) as document:
            pages = document.pages
            if not 1 <= len(pages) <= MAX_PAGES:
                raise PdfIntakeError("PDF page count exceeds the supported limit")
            for index, page in enumerate(pages):
                # Object access may decode streams, so ingestion must ultimately
                # run in a resource-limited worker, NOT on a request thread.
                vectors = sum(
                    _bounded_length(getattr(page, category, []), category)
                    for category in ("lines", "rects", "curves")
                )
                images = _bounded_length(getattr(page, "images", []), "images")
                chars = _bounded_length(getattr(page, "chars", []), "chars")
                width, height = float(page.width), float(page.height)
                if (
                    not 0 < width <= 20_000
                    or not 0 < height <= 20_000
                ):
                    raise PdfIntakeError("Unsupported or unbounded PDF page dimensions")
                kind: PageKind = (
                    "mixed" if vectors and images else
                    "vector" if vectors else
                    "raster" if images else "unknown"
                )
                results.append(PageInspection(index, width, height, kind,
                                              vectors, images, chars))
    except (PdfIntakeError, PdfParserUnavailable):
        raise
    except Exception as exc:
        # Never reflect file contents, exception payloads or private plan text.
        raise PdfIntakeError("PDF parser could not safely inspect this document") from exc
    return PdfInspection(
        hashlib.sha256(payload).hexdigest(), len(payload), tuple(results), permission
    )
