"""Typed Pydantic schemas for MF-015: Brand asset inventory and Gemini visual design proposals.

Security & Integrity Invariants:
1. Gemini NEVER outputs executable HTML, CSS, or JavaScript. Every recommendation is a
   strictly typed ``ProposalChange`` with a ``ProposalComponentConfig`` validated against
   allowlisted component IDs, layout presets, and ``sitegen.COLORS`` palette keys.
2. Every ``ProposalChange`` must link to at least one verified ``EvidenceItem.id`` from the
   deterministic scanner and may only reference ``BrandAssetCandidate.id`` values that passed
   safety checks.
3. No unverified pricing, labor-hour estimates, permit determinations, search ranking promises,
   or conversion rate guarantees are permitted in any proposal field.
4. All discovered external media starts as ``license_state='unverified_candidate'`` and cannot
   be treated as permanent tenant property without explicit owner attestation.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..services import sitegen
from .preview import ObservationStatus

AssetKind = Literal[
    "logo",
    "hero_image",
    "gallery_image",
    "og_image",
    "favicon",
    "apple_touch_icon",
    "css_background",
]
LicenseState = Literal[
    "unverified_candidate",
    "owner_attested",
    "measuredfield_stock",
    "rejected",
]
VerificationState = Literal[
    "discovered_html",
    "sniffed_safe_image",
    "rejected_unsafe",
    "unreachable",
]
ComponentId = Literal[
    "hero_banner",
    "brand_header",
    "primary_cta",
    "service_cards",
    "scope_explorer",
    "contact_intake",
    "trust_summary",
]
ChangeType = Literal[
    "headline_clarity",
    "cta_prominence",
    "tap_to_call_placement",
    "structured_scope_intake",
    "service_area_definition",
    "visual_hierarchy",
    "mobile_readiness",
]
ConfidenceLevel = Literal["high", "medium", "low", "unknown"]
LayoutPreset = Literal[
    "classic_service",
    "split_hero_intake",
    "craft_showcase",
    "direct_dispatch",
]
CtaTarget = Literal["#contact", "#scope-explorer", "#services", "tel_link"]

SAFE_RASTER_MIMES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
)

LAYOUT_PRESETS: tuple[LayoutPreset, ...] = (
    "classic_service",
    "split_hero_intake",
    "craft_showcase",
    "direct_dispatch",
)

# Rejects raw HTML/SVG/script/style/CSS-url injection in any AI-supplied text field
_EXECUTABLE_MARKUP_RE = re.compile(
    r"(<\s*/?\s*[a-zA-Z!/?]|javascript\s*:|vbscript\s*:|data\s*:\s*text/html"
    r"|on[a-z]{3,16}\s*=|url\s*\(|@import\b|expression\s*\()",
    re.IGNORECASE,
)

# Rejects unverified financial, labor, permit, ranking, or conversion claims
_FORBIDDEN_PROPOSAL_CLAIM_RE = re.compile(
    r"(\$\s*\d+|\b\d+\s*(?:usd|dollars?)\b|\b\d+\s*(?:hrs?|hours?)\s*(?:of\s+labor|estimate)\b"
    r"|\bguaranteed?\s+#?1\b|\bpage\s+1\s+on\s+google\b|\brank\s*#\s*\d+\b"
    r"|\b\d+(?:\.\d+)?%\s*(?:more\s+leads|conversion|increase|lift)\b"
    r"|\bno\s+permit\s+required\b|\blicensed\s+and\s+bonded\b)",
    re.IGNORECASE,
)

DEFAULT_AI_DISCLOSURE = (
    "Concept layout recommendations were generated from bounded static HTML observations and "
    "candidate brand asset metadata. No live customer data was accessed, no search rankings or "
    "conversion outcomes are promised, and all content, imagery rights, and settings require "
    "business owner confirmation before publication."
)


def _clean_text(value: str, limit: int) -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def _validate_safe_text(value: str, limit: int, field_label: str) -> str:
    cleaned = _clean_text(value, limit)
    if _EXECUTABLE_MARKUP_RE.search(cleaned):
        raise ValueError(f"Executable HTML, script, or CSS syntax is forbidden in {field_label}.")
    if _FORBIDDEN_PROPOSAL_CLAIM_RE.search(cleaned):
        raise ValueError(
            f"Unverified pricing, ranking, conversion, permit, or license claims are forbidden in {field_label}."
        )
    return cleaned


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


class ImageDimensions(BaseModel):
    """Detected pixel width and height for a raster image candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)


class BrandAssetCandidate(BaseModel):
    """A candidate logo, icon, or image reference discovered on a public webpage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^asset_[a-z0-9_]{2,40}$")
    source_page: str = Field(min_length=8, max_length=500)
    source_url: str = Field(min_length=8, max_length=500)
    kind: AssetKind
    mime: str = Field(default="unknown", max_length=80)
    dimensions: ImageDimensions | None = None
    alt_text: str = Field(default="", max_length=200)
    confidence: ConfidenceLevel = "medium"
    license_state: LicenseState = "unverified_candidate"
    verification_state: VerificationState = "discovered_html"
    discovery_source: str = Field(default="img[src]", max_length=60)
    byte_size: int | None = Field(default=None, ge=0, le=10_485_760)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("source_page")
    @classmethod
    def _check_source_page(cls, v: str) -> str:
        return _validate_http_url(v, "source_page")

    @field_validator("source_url")
    @classmethod
    def _check_source_url(cls, v: str) -> str:
        return _validate_http_url(v, "source_url")

    @field_validator("alt_text")
    @classmethod
    def _clean_alt(cls, v: str) -> str:
        cleaned = _clean_text(v, 200)
        # Strip any angle brackets from untrusted alt attributes
        cleaned = re.sub(r"[<>]", "", cleaned)
        return cleaned

    @model_validator(mode="after")
    def _enforce_svg_and_safety_rules(self) -> BrandAssetCandidate:
        lower_url = self.source_url.lower().split("?")[0]
        lower_mime = self.mime.lower()
        if (
            lower_url.endswith((".svg", ".svgz"))
            or "svg" in lower_mime
            or "html" in lower_mime
            or "javascript" in lower_mime
        ) and self.verification_state == "sniffed_safe_image":
            raise ValueError("SVG or active markup files cannot be marked as sniffed_safe_image.")
        return self


class EvidenceItem(BaseModel):
    """Structured diagnostic evidence item passed to Gemini and referenced by proposals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^ev_[a-z0-9_]{2,40}$")
    source_field: str = Field(min_length=2, max_length=60)
    status: ObservationStatus
    summary: str = Field(min_length=3, max_length=300)
    observed_value: str = Field(default="", max_length=300)

    @field_validator("summary", "observed_value")
    @classmethod
    def _sanitize_evidence_text(cls, v: str) -> str:
        cleaned = _clean_text(v, 300)
        return re.sub(r"[<>]", "", cleaned)


class ProposalComponentConfig(BaseModel):
    """Allowlisted, non-executable configuration parameters for a single UI component."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    heading_text: str = Field(default="", max_length=90)
    subheading_text: str = Field(default="", max_length=240)
    cta_label: str = Field(default="", max_length=50)
    cta_target: CtaTarget = "#contact"
    asset_id: str | None = Field(default=None, pattern=r"^asset_[a-z0-9_]{2,40}$")
    palette_key: str = Field(default="ocean", max_length=20)
    layout_preset: LayoutPreset = "split_hero_intake"
    show_tap_to_call: bool = False
    show_scope_explorer: bool = True
    requires_owner_confirmation: bool = True

    @field_validator("heading_text")
    @classmethod
    def _check_heading(cls, v: str) -> str:
        return _validate_safe_text(v, 90, "heading_text") if v else ""

    @field_validator("subheading_text")
    @classmethod
    def _check_subheading(cls, v: str) -> str:
        return _validate_safe_text(v, 240, "subheading_text") if v else ""

    @field_validator("cta_label")
    @classmethod
    def _check_cta(cls, v: str) -> str:
        return _validate_safe_text(v, 50, "cta_label") if v else ""

    @field_validator("palette_key")
    @classmethod
    def _check_palette(cls, v: str) -> str:
        key = (v or "ocean").strip().lower()
        if key not in sitegen.COLORS:
            raise ValueError(f"Unsupported palette_key {key!r}; must be one of {tuple(sitegen.COLORS)}.")
        return key


class ProposalChange(BaseModel):
    """A single evidence-linked component recommendation in a visual design proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    change_id: str = Field(pattern=r"^chg_[a-z0-9_]{2,40}$")
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    component_id: ComponentId
    change_type: ChangeType
    title: str = Field(min_length=3, max_length=120)
    rationale: str = Field(min_length=5, max_length=400)
    before_state: str = Field(min_length=3, max_length=300)
    after_config: ProposalComponentConfig
    confidence: ConfidenceLevel = "medium"
    is_claim: Literal[False] = False

    @field_validator("evidence_ids")
    @classmethod
    def _check_evidence_id_format(cls, v: list[str]) -> list[str]:
        for ev_id in v:
            if not re.fullmatch(r"ev_[a-z0-9_]{2,40}", ev_id or ""):
                raise ValueError(f"Invalid evidence_id format: {ev_id!r}")
        return list(dict.fromkeys(v))

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        return _validate_safe_text(v, 120, "title")

    @field_validator("rationale")
    @classmethod
    def _check_rationale(cls, v: str) -> str:
        return _validate_safe_text(v, 400, "rationale")

    @field_validator("before_state")
    @classmethod
    def _check_before(cls, v: str) -> str:
        return _validate_safe_text(v, 300, "before_state")


class VisualDesignProposal(BaseModel):
    """Top-level schema-validated visual design proposal returned by Gemini or fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    source_url: str = Field(min_length=8, max_length=500)
    evidence_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_id: str = Field(default="deterministic-fallback", min_length=2, max_length=80)
    recommended_palette: str = Field(default="ocean", max_length=20)
    recommended_layout: LayoutPreset = "split_hero_intake"
    selected_logo_asset_id: str | None = Field(default=None, pattern=r"^asset_[a-z0-9_]{2,40}$")
    selected_hero_asset_id: str | None = Field(default=None, pattern=r"^asset_[a-z0-9_]{2,40}$")
    asset_ids: list[str] = Field(default_factory=list, max_length=16)
    changes: list[ProposalChange] = Field(default_factory=list, max_length=8)
    warnings: list[str] = Field(default_factory=list, max_length=12)
    unknowns: list[str] = Field(
        default_factory=lambda: [
            "Ownership and redistribution rights for discovered logos and photographs",
            "JavaScript-rendered UI components not present in initial static HTML",
            "Actual visitor conversion rates, search rankings, and response times",
        ],
        max_length=12,
    )
    ai_disclosure: str = Field(default=DEFAULT_AI_DISCLOSURE, min_length=20, max_length=600)

    @field_validator("source_url")
    @classmethod
    def _check_source_url(cls, v: str) -> str:
        return _validate_http_url(v, "source_url")

    @field_validator("recommended_palette")
    @classmethod
    def _check_palette(cls, v: str) -> str:
        key = (v or "ocean").strip().lower()
        if key not in sitegen.COLORS:
            raise ValueError(f"Unsupported recommended_palette {key!r}")
        return key

    @field_validator("warnings", "unknowns")
    @classmethod
    def _clean_lists(cls, items: list[str]) -> list[str]:
        return [_validate_safe_text(item, 300, "list_item") for item in items if item and item.strip()]

    @field_validator("ai_disclosure")
    @classmethod
    def _clean_disclosure(cls, v: str) -> str:
        return _validate_safe_text(v, 600, "ai_disclosure")


def validate_proposal_against_inventory(
    proposal: VisualDesignProposal,
    evidence_items: Sequence[EvidenceItem],
    assets: Sequence[BrandAssetCandidate],
) -> VisualDesignProposal:
    """Verify that every evidence_id and asset_id in ``proposal`` exists and is safe."""
    evidence_by_id = {ev.id: ev for ev in evidence_items}
    safe_assets_by_id = {
        a.id: a
        for a in assets
        if a.verification_state != "rejected_unsafe" and a.license_state != "rejected"
    }

    for aid in proposal.asset_ids:
        if aid not in safe_assets_by_id:
            raise ValueError(f"Proposal references unknown or rejected asset_id: {aid!r}")

    if proposal.selected_logo_asset_id and proposal.selected_logo_asset_id not in safe_assets_by_id:
        raise ValueError(
            f"selected_logo_asset_id {proposal.selected_logo_asset_id!r} is not in safe asset inventory."
        )
    if proposal.selected_hero_asset_id and proposal.selected_hero_asset_id not in safe_assets_by_id:
        raise ValueError(
            f"selected_hero_asset_id {proposal.selected_hero_asset_id!r} is not in safe asset inventory."
        )

    seen_change_ids: set[str] = set()
    for chg in proposal.changes:
        if chg.change_id in seen_change_ids:
            raise ValueError(f"Duplicate change_id in proposal: {chg.change_id!r}")
        seen_change_ids.add(chg.change_id)

        for ev_id in chg.evidence_ids:
            if ev_id not in evidence_by_id:
                raise ValueError(
                    f"ProposalChange {chg.change_id!r} references unknown evidence_id: {ev_id!r}"
                )

        # If all linked evidence items are unable_to_determine, a change cannot assert missing features
        linked_statuses = {evidence_by_id[eid].status for eid in chg.evidence_ids}
        if linked_statuses == {"unable_to_determine"}:
            raise ValueError(
                f"ProposalChange {chg.change_id!r} cannot recommend changes based solely on unable_to_determine evidence."
            )

        cfg_asset = chg.after_config.asset_id
        if cfg_asset is not None and cfg_asset not in safe_assets_by_id:
            raise ValueError(
                f"ProposalChange {chg.change_id!r} references unknown or unsafe asset_id: {cfg_asset!r}"
            )

    return proposal
