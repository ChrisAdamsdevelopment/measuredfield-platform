"""Typed Pydantic schemas for MF-016: Owner-attested brand media ingestion and expiring same-origin serving.

Security & Integrity Invariants:
1. No third-party candidate image may be downloaded in full, decoded, or stored unless the
   caller supplies a validated ``OwnerMediaAttestation`` with ``owner_attested_rights=True``.
2. Only clean, re-encoded raster formats (``image/png``, ``image/jpeg``, ``image/webp``) are
   permitted. SVG, HTML, XML, script, and GIF payloads are never accepted for preview serving.
3. Every stored ``IngestedPreviewMedia`` record exposes only a scoped, expiring same-origin
   URL path (``/dev/visual-preview/media/<session_id>/<media_token>``) and never a third-party URL.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

AllowedOutputRasterMime = Literal["image/png", "image/jpeg", "image/webp"]
MediaSlotRole = Literal["header_logo", "hero_visual"]

_SAFE_SAME_ORIGIN_MEDIA_PATH_RE = re.compile(
    r"^/dev/visual-preview/media/[a-f0-9]{24,64}/[A-Za-z0-9_\-]{32,96}$"
)


def _validate_http_url(url: str, field_label: str) -> str:
    candidate = (url or "").strip()
    if not candidate or len(candidate) > 500 or any(ord(c) < 33 or ord(c) > 126 for c in candidate):
        raise ValueError(f"Invalid {field_label} URL.")
    if any(ch in candidate for ch in ("<", ">", '"', "'", "\\")):
        raise ValueError(f"Unsafe characters in {field_label} URL.")
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{field_label} must be an absolute http or https URL.")
    if parts.username or parts.password:
        raise ValueError(f"{field_label} must not contain credentials.")
    return candidate


class OwnerMediaAttestation(BaseModel):
    """Explicit owner attestation required before downloading or re-encoding a candidate brand image."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(pattern=r"^asset_[a-z0-9_]{2,40}$")
    source_page: str = Field(min_length=8, max_length=500)
    source_url: str = Field(min_length=8, max_length=500)
    slot_role: MediaSlotRole
    owner_attested_rights: Literal[True]
    attested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("source_page")
    @classmethod
    def _check_source_page(cls, v: str) -> str:
        return _validate_http_url(v, "source_page")

    @field_validator("source_url")
    @classmethod
    def _check_source_url(cls, v: str) -> str:
        return _validate_http_url(v, "source_url")


class IngestedPreviewMedia(BaseModel):
    """Metadata for a clean, metadata-stripped raster image stored in the expiring preview store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    media_token: str = Field(pattern=r"^[A-Za-z0-9_\-]{32,96}$")
    session_id: str = Field(pattern=r"^[a-f0-9]{24,64}$")
    asset_id: str = Field(pattern=r"^asset_[a-z0-9_]{2,40}$")
    slot_role: MediaSlotRole
    content_type: AllowedOutputRasterMime
    width: int = Field(ge=1, le=2400)
    height: int = Field(ge=1, le=2400)
    byte_size: int = Field(ge=1, le=2_097_152)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alt_text: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    same_origin_path: str = Field(min_length=40, max_length=220)

    @field_validator("alt_text")
    @classmethod
    def _clean_alt(cls, v: str) -> str:
        cleaned = re.sub(r"[\x00-\x1f<>]", " ", str(v or ""))
        return re.sub(r"\s+", " ", cleaned).strip()[:200]

    @field_validator("same_origin_path")
    @classmethod
    def _check_same_origin_path(cls, v: str) -> str:
        path = (v or "").strip()
        if ".." in path or "//" in path or "\\" in path:
            raise ValueError("Path traversal or authority tricks forbidden in same_origin_path.")
        if not _SAFE_SAME_ORIGIN_MEDIA_PATH_RE.fullmatch(path):
            raise ValueError(
                "same_origin_path must match /dev/visual-preview/media/<session_id>/<media_token>."
            )
        return path
