"""Safe owner-attested brand media download, raster re-encoding, and expiring same-origin store (MF-016).

Security & Privacy Invariants:
1. **Explicit Owner Attestation Gate**: No candidate third-party image is downloaded in full or
   stored unless ``OwnerMediaAttestation(owner_attested_rights=True)`` is verified first.
2. **SSRF & DNS Rebinding Defense**: Full media downloads use MF-014's ``validate_and_resolve_url()``,
   ``build_pinned_request()``, and ``PinnedPublicIPTransport`` with redirect re-resolution and IP
   pinning on every hop (max 2 redirects).
3. **Strict Raster Decode & Metadata Stripping**: Only ``PNG``, ``JPEG``, and ``WEBP`` raster
   formats are accepted. ``SVG``, ``HTML``, ``XML``, ``GIF``, and active/polyglot files are
   rejected before and during decode. Pillow's ``MAX_IMAGE_PIXELS`` guard blocks decompression
   bombs. Images are copied pixel-by-pixel into a clean canvas and re-encoded to strip all
   EXIF, GPS, ICC, XMP, and comment metadata. Untrusted original bytes are never stored.
4. **Scoped Expiring Same-Origin Serving**: Re-encoded media is stored only in a bounded,
   expiring ``PreviewMediaStore`` keyed by ``(session_id, media_token)`` and served from
   ``/dev/visual-preview/media/<session_id>/<media_token>`` with ``Cache-Control: no-store``.
"""
from __future__ import annotations

import hashlib
import io
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal
from urllib.parse import urljoin

import httpx
from PIL import Image, ImageDraw, ImageOps, TiffImagePlugin

from ..schemas.ai_visual import BrandAssetCandidate
from ..schemas.preview_media import (
    AllowedOutputRasterMime,
    IngestedPreviewMedia,
    MediaSlotRole,
    OwnerMediaAttestation,
)
from .brand_assets import _VerifiedTransportWrapper
from .scanner import (
    PinnedPublicIPTransport,
    SSRFBlockedError,
    build_pinned_request,
    validate_and_resolve_url,
)

MAX_MEDIA_DOWNLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB hard download cap
MAX_DECODED_PIXELS = 12_000_000  # 12 megapixels hard decode cap
MAX_INPUT_DIMENSION_PX = 4096
MEDIA_TTL_SECONDS = 900  # 15 minutes default expiry
MAX_MEDIA_REDIRECTS = 2
MAX_STORE_ITEMS = 64

_SESSION_ID_RE = re.compile(r"^[a-f0-9]{24,64}$")
_MEDIA_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{32,96}$")

_FORBIDDEN_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"<svg",
    b"<?xml",
    b"<!doctype",
    b"<html",
    b"<script",
    b"<!--",
    b"gif87a",
    b"gif89a",
    b"\x00\x00\x01\x00",  # ICO
)


class PreviewMediaError(Exception):
    """Base exception for MF-016 brand media ingestion failures."""


class UnattestedMediaError(PreviewMediaError):
    """Raised when media download or storage is attempted without owner rights attestation."""


class UnsafeMediaError(PreviewMediaError):
    """Raised when a media payload fails format, size, pixel, or decompression safety checks."""


def _is_forbidden_magic_or_svg(data: bytes, url: str = "", content_type: str = "") -> bool:
    lower_url = (url or "").lower().split("?")[0].split("#")[0]
    lower_ct = (content_type or "").lower()
    if lower_url.endswith((".svg", ".svgz", ".gif", ".html", ".htm", ".xml", ".js")):
        return True
    if any(tok in lower_ct for tok in ("svg", "gif", "html", "xml", "javascript", "text/")):
        return True
    head = data[:512].lstrip().lower()
    if any(head.startswith(p) for p in _FORBIDDEN_MAGIC_PREFIXES):
        return True
    if b"<svg" in head or b"<script" in head or b"javascript:" in head:
        return True
    return False


def reencode_clean_raster_bytes(
    raw_bytes: bytes,
    *,
    slot_role: MediaSlotRole,
    max_bytes: int = MAX_MEDIA_DOWNLOAD_BYTES,
    max_pixels: int = MAX_DECODED_PIXELS,
    max_dimension: int = MAX_INPUT_DIMENSION_PX,
) -> tuple[bytes, AllowedOutputRasterMime, int, int]:
    """Safely decode ``raw_bytes`` (PNG/JPEG/WEBP only), strip all metadata, and re-encode.

    Returns ``(clean_bytes, content_type, width, height)``.
    """
    if not raw_bytes or len(raw_bytes) < 16:
        raise UnsafeMediaError("Media payload is empty or truncated.")
    if len(raw_bytes) > max_bytes:
        raise UnsafeMediaError(f"Media payload exceeds {max_bytes} byte limit.")
    if _is_forbidden_magic_or_svg(raw_bytes):
        raise UnsafeMediaError("Active markup, SVG, ICO, or GIF formats are forbidden.")

    prev_max_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        try:
            with Image.open(io.BytesIO(raw_bytes), formats=["PNG", "JPEG", "WEBP"]) as src:
                fmt = (src.format or "").upper()
                if fmt not in ("PNG", "JPEG", "WEBP"):
                    raise UnsafeMediaError(f"Unsupported raster format: {fmt!r}")
                width, height = src.size
                if width < 1 or height < 1 or width > max_dimension or height > max_dimension:
                    raise UnsafeMediaError(
                        f"Image dimensions {width}x{height} exceed allowed bounds (max {max_dimension}px)."
                    )
                if width * height > max_pixels:
                    raise UnsafeMediaError(
                        f"Decoded pixel count {width * height} exceeds limit ({max_pixels})."
                    )
                src.load()
                transposed = ImageOps.exif_transpose(src)

                has_alpha = transposed.mode in ("RGBA", "LA") or (
                    transposed.mode == "P" and "transparency" in transposed.info
                )
                target_mode = "RGBA" if (has_alpha and slot_role == "header_logo") else "RGB"
                converted = transposed.convert(target_mode)

                # Copy raw pixel bytes into a fresh Image canvas with zero EXIF/ICC/XMP metadata
                clean_canvas = Image.frombytes(target_mode, converted.size, converted.tobytes())
        except UnsafeMediaError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise UnsafeMediaError("Decompression bomb detected in image payload.") from exc
        except Exception as exc:
            raise UnsafeMediaError(f"Failed to decode valid raster image: {type(exc).__name__}") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = prev_max_pixels

    max_box = (480, 180) if slot_role == "header_logo" else (1280, 960)
    clean_canvas.thumbnail(max_box, Image.Resampling.LANCZOS)
    out_w, out_h = clean_canvas.size

    out_buf = io.BytesIO()
    if slot_role == "header_logo" and clean_canvas.mode == "RGBA":
        clean_canvas.save(out_buf, format="PNG", optimize=True)
        out_mime: AllowedOutputRasterMime = "image/png"
    else:
        rgb_canvas = clean_canvas if clean_canvas.mode == "RGB" else clean_canvas.convert("RGB")
        rgb_canvas.save(out_buf, format="WEBP", quality=86, method=4)
        out_mime = "image/webp"

    clean_bytes = out_buf.getvalue()
    if not clean_bytes or len(clean_bytes) > max_bytes:
        raise UnsafeMediaError("Re-encoded image exceeded byte limits.")
    return clean_bytes, out_mime, out_w, out_h


@dataclass
class _StoredMediaRecord:
    meta: IngestedPreviewMedia
    data: bytes


class PreviewMediaStore:
    """Bounded, expiring in-memory store for owner-attested, re-encoded preview media."""

    def __init__(self, *, max_items: int = MAX_STORE_ITEMS) -> None:
        self.max_items = max_items
        self._records: dict[tuple[str, str], _StoredMediaRecord] = {}

    def purge_expired(self, now_dt: datetime | None = None) -> int:
        current = now_dt or datetime.now(timezone.utc)
        expired_keys = [
            key for key, rec in self._records.items() if rec.meta.expires_at <= current
        ]
        for key in expired_keys:
            self._records.pop(key, None)
        return len(expired_keys)

    def purge_session(self, session_id: str) -> int:
        keys = [key for key in self._records if key[0] == session_id]
        for key in keys:
            self._records.pop(key, None)
        return len(keys)

    def put_media(
        self,
        *,
        session_id: str,
        asset_id: str,
        slot_role: MediaSlotRole,
        content_type: AllowedOutputRasterMime,
        width: int,
        height: int,
        clean_bytes: bytes,
        alt_text: str = "",
        ttl_seconds: int = MEDIA_TTL_SECONDS,
        now_dt: datetime | None = None,
    ) -> IngestedPreviewMedia:
        if not _SESSION_ID_RE.fullmatch(session_id or ""):
            raise ValueError("Invalid session_id format for PreviewMediaStore.")
        current = now_dt or datetime.now(timezone.utc)
        self.purge_expired(current)

        # Remove any previous entry for the same (session_id, slot_role) so re-runs don't leak memory
        stale_slot_keys = [
            key
            for key, rec in self._records.items()
            if key[0] == session_id and rec.meta.slot_role == slot_role
        ]
        for key in stale_slot_keys:
            self._records.pop(key, None)

        while len(self._records) >= self.max_items:
            oldest_key = min(
                self._records.keys(), key=lambda k: self._records[k].meta.created_at
            )
            self._records.pop(oldest_key, None)

        media_token = secrets.token_urlsafe(32)
        same_origin_path = f"/dev/visual-preview/media/{session_id}/{media_token}"
        meta = IngestedPreviewMedia(
            media_token=media_token,
            session_id=session_id,
            asset_id=asset_id,
            slot_role=slot_role,
            content_type=content_type,
            width=width,
            height=height,
            byte_size=len(clean_bytes),
            sha256=hashlib.sha256(clean_bytes).hexdigest(),
            alt_text=alt_text,
            created_at=current,
            expires_at=current + timedelta(seconds=max(1, ttl_seconds)),
            same_origin_path=same_origin_path,
        )
        self._records[(session_id, media_token)] = _StoredMediaRecord(meta=meta, data=clean_bytes)
        return meta

    def get_media(
        self,
        session_id: str,
        media_token: str,
        *,
        now_dt: datetime | None = None,
    ) -> tuple[IngestedPreviewMedia, bytes] | None:
        if not _SESSION_ID_RE.fullmatch(session_id or "") or not _MEDIA_TOKEN_RE.fullmatch(
            media_token or ""
        ):
            return None
        current = now_dt or datetime.now(timezone.utc)
        self.purge_expired(current)
        rec = self._records.get((session_id, media_token))
        if rec is None or rec.meta.expires_at <= current:
            return None
        return rec.meta, rec.data

    def list_session_media(
        self,
        session_id: str,
        *,
        now_dt: datetime | None = None,
    ) -> dict[str, IngestedPreviewMedia]:
        if not _SESSION_ID_RE.fullmatch(session_id or ""):
            return {}
        current = now_dt or datetime.now(timezone.utc)
        self.purge_expired(current)
        result: dict[str, IngestedPreviewMedia] = {}
        for (sid, _tok), rec in self._records.items():
            if sid == session_id:
                result[rec.meta.asset_id] = rec.meta
        return result

    def clear(self) -> None:
        self._records.clear()

    def count(self) -> int:
        return len(self._records)


DEFAULT_PREVIEW_MEDIA_STORE = PreviewMediaStore()


def download_and_ingest_attested_media(
    candidate: BrandAssetCandidate,
    attestation: OwnerMediaAttestation | None,
    *,
    session_id: str,
    store: PreviewMediaStore | None = None,
    resolver: Callable[..., Any] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 5.0,
    max_bytes: int = MAX_MEDIA_DOWNLOAD_BYTES,
    max_pixels: int = MAX_DECODED_PIXELS,
    ttl_seconds: int = MEDIA_TTL_SECONDS,
    now_dt: datetime | None = None,
) -> IngestedPreviewMedia:
    """Verify owner attestation, download candidate raster over pinned IP, re-encode, and store."""
    if attestation is None or attestation.owner_attested_rights is not True:
        raise UnattestedMediaError(
            "Owner rights attestation is required before downloading or storing brand media."
        )
    if attestation.asset_id != candidate.id:
        raise UnattestedMediaError(
            f"Attestation asset_id {attestation.asset_id!r} does not match candidate {candidate.id!r}."
        )
    if attestation.source_url != candidate.source_url:
        raise UnattestedMediaError("Attestation source_url does not match candidate source_url.")
    if candidate.verification_state == "rejected_unsafe" or _is_forbidden_magic_or_svg(
        b"", url=candidate.source_url, content_type=candidate.mime
    ):
        raise UnsafeMediaError(f"Candidate {candidate.id!r} is marked unsafe or non-raster.")

    target_store = store if store is not None else DEFAULT_PREVIEW_MEDIA_STORE
    current_target = validate_and_resolve_url(candidate.source_url, resolver=resolver)
    active_transport: httpx.BaseTransport = (
        _VerifiedTransportWrapper(transport)
        if transport is not None
        else PinnedPublicIPTransport()
    )
    timeout_cfg = httpx.Timeout(connect=min(timeout, 3.0), read=timeout, write=2.0, pool=2.0)

    try:
        with httpx.Client(
            transport=active_transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_cfg,
        ) as client:
            redirects = 0
            while True:
                request = build_pinned_request(current_target)
                request.headers["Accept"] = "image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.1"
                try:
                    response = client.send(request, stream=True)
                except SSRFBlockedError:
                    raise
                except Exception as exc:
                    raise UnsafeMediaError(f"Media download failed: {type(exc).__name__}") from exc

                try:
                    status_code = response.status_code
                    if status_code in (301, 302, 303, 307, 308):
                        location = (response.headers.get("location") or "").strip()
                        if not location or redirects >= MAX_MEDIA_REDIRECTS:
                            raise UnsafeMediaError("Media redirect limit exceeded or missing Location.")
                        next_url = urljoin(current_target.normalized_url, location)
                        if _is_forbidden_magic_or_svg(b"", url=next_url):
                            raise UnsafeMediaError("Redirected to forbidden SVG/GIF/markup URL.")
                        current_target = validate_and_resolve_url(next_url, resolver=resolver)
                        redirects += 1
                        continue

                    if status_code != 200:
                        raise UnsafeMediaError(f"Media upstream returned HTTP {status_code}.")

                    ct_header = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if _is_forbidden_magic_or_svg(b"", url=current_target.normalized_url, content_type=ct_header):
                        raise UnsafeMediaError(f"Forbidden media Content-Type: {ct_header!r}")

                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise UnsafeMediaError(
                                f"Media stream exceeded maximum allowed size ({max_bytes} bytes)."
                            )
                        chunks.append(chunk)
                    raw_bytes = b"".join(chunks)
                finally:
                    response.close()
                break
    finally:
        if transport is None:
            active_transport.close()

    clean_bytes, out_mime, out_w, out_h = reencode_clean_raster_bytes(
        raw_bytes,
        slot_role=attestation.slot_role,
        max_bytes=max_bytes,
        max_pixels=max_pixels,
    )
    return target_store.put_media(
        session_id=session_id,
        asset_id=candidate.id,
        slot_role=attestation.slot_role,
        content_type=out_mime,
        width=out_w,
        height=out_h,
        clean_bytes=clean_bytes,
        alt_text=candidate.alt_text,
        ttl_seconds=ttl_seconds,
        now_dt=now_dt,
    )


def generate_synthetic_fixture_media_bytes(
    kind: Literal["logo", "hero"],
    *,
    include_exif: bool = False,
) -> bytes:
    """Generate deterministic raster bytes (PNG logo or JPEG/WebP hero photo) for offline demo/tests."""
    buf = io.BytesIO()
    if kind == "logo":
        img = Image.new("RGBA", (320, 96), (24, 53, 60, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((10, 10, 86, 86), radius=14, fill=(180, 92, 52, 255))
        draw.rectangle((98, 28, 300, 46), fill=(238, 245, 243, 255))
        draw.rectangle((98, 56, 245, 68), fill=(166, 202, 195, 255))
        img.save(buf, format="PNG")
        return buf.getvalue()

    img = Image.new("RGB", (960, 540), (232, 241, 238))
    draw = ImageDraw.Draw(img)
    # Architectural timber & stone hero illustration
    draw.rectangle((0, 380, 960, 540), fill=(48, 76, 82))
    draw.rectangle((80, 110, 880, 410), fill=(250, 247, 240), outline=(145, 92, 58), width=6)
    draw.rectangle((140, 160, 460, 370), fill=(184, 121, 78))
    draw.rectangle((500, 160, 820, 370), fill=(76, 118, 126))
    if include_exif:
        exif = Image.Exif()
        exif[270] = "Synthetic Contractor Camera EXIF Description"
        exif[305] = "Private Camera Firmware v1.0"
        img.save(buf, format="JPEG", quality=88, exif=exif.tobytes())
    else:
        img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
