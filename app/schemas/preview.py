"""Typed schemas and integration adapters for the Track B diagnostic & preview engine.

Enforces three core invariants:
1. Tri-state diagnostic observations (``observed``, ``not_observed``, ``unable_to_determine``)
   with explicit evidence provenance (``system_recorded`` vs ``unknown``; never falsely
   labeled ``owner_stated``).
2. Strict separation between partial unclaimed preview concepts (``PreviewConceptProfile``)
   and owner-confirmed website profiles validated by ``app.services.sitegen.validate_profile``.
3. Zero fabricated pricing, labor-hour estimates, license/permit determinations, or search
   ranking claims in unverified demo widgets or diagnostic cards.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..services import assessment, sitegen

ObservationStatus = Literal["observed", "not_observed", "unable_to_determine"]
EvidenceStatus = Literal["owner_stated", "system_recorded", "measured", "unknown"]
ScanState = Literal["completed", "unreachable", "http_error", "non_html", "blocked_ssrf"]
EntryPath = Literal["existing_site", "new_business", "operations_only", "legacy_assessment"]

OBSERVATION_STATUSES: tuple[ObservationStatus, ...] = (
    "observed",
    "not_observed",
    "unable_to_determine",
)
EVIDENCE_STATUSES: tuple[EvidenceStatus, ...] = (
    "owner_stated",
    "system_recorded",
    "measured",
    "unknown",
)
SCAN_STATES: tuple[ScanState, ...] = (
    "completed",
    "unreachable",
    "http_error",
    "non_html",
    "blocked_ssrf",
)
ENTRY_PATHS: tuple[EntryPath, ...] = (
    "existing_site",
    "new_business",
    "operations_only",
    "legacy_assessment",
)

INDUSTRY_ALIASES: dict[str, str] = {
    "remodeling": "construction",
    "contractor": "construction",
    "general_contractor": "construction",
    "trades": "construction",
    "home_services": "construction",
    "janitorial": "cleaning",
    "house_cleaning": "cleaning",
    "commercial_cleaning": "cleaning",
    "professional_services": "professional",
    "consulting": "professional",
    "accounting": "professional",
    "legal": "professional",
    "shop": "retail",
    "store": "retail",
    "ecommerce": "retail",
    "lawn_care": "landscaping",
    "grounds": "landscaping",
    "restaurant": "food",
    "bakery": "food",
    "catering": "food",
    "fitness": "wellness",
    "salon": "wellness",
    "spa": "wellness",
    "general": "other",
}

RESPONSE_WINDOW_ALIASES: dict[str, str] = {
    "none": "no_promise",
    "no_promise": "no_promise",
    "1_business_day": "one_business_day",
    "one_day": "one_business_day",
    "24_hours": "one_business_day",
    "one_business_day": "one_business_day",
    "2_business_days": "two_business_days",
    "two_days": "two_business_days",
    "48_hours": "two_business_days",
    "two_business_days": "two_business_days",
    "3_business_days": "three_business_days",
    "three_days": "three_business_days",
    "72_hours": "three_business_days",
    "three_business_days": "three_business_days",
}

DEFAULT_SERVICES_BY_INDUSTRY: dict[str, list[dict[str, str]]] = {
    "construction": [
        {
            "name": "Remodeling & Repairs",
            "description": "Share your project scope, rooms, and timeline for an owner review.",
        },
        {
            "name": "Site Visit & Scope Review",
            "description": "Walk through measurements, site access, and material selections together.",
        },
    ],
    "cleaning": [
        {
            "name": "Recurring Home or Office Cleaning",
            "description": "Tell us the property type, rooms, and preferred schedule.",
        },
        {
            "name": "Deep or Move-In / Move-Out Cleaning",
            "description": "Outline priority areas and target dates for a custom scope check.",
        },
    ],
    "professional": [
        {
            "name": "Initial Consultation & Scope Review",
            "description": "Describe your goals, current records, and timeline for next steps.",
        },
        {
            "name": "Ongoing Advisory & Project Support",
            "description": "Structured follow-up and clear deliverables tailored to your needs.",
        },
    ],
    "retail": [
        {
            "name": "Product Availability & Custom Orders",
            "description": "Ask about current inventory, special requests, or pickup timing.",
        },
        {
            "name": "Bulk or Local Business Inquiries",
            "description": "Share quantities and target dates so we can confirm options.",
        },
    ],
    "landscaping": [
        {
            "name": "Grounds Care & Outdoor Projects",
            "description": "Share your yard or property details and preferred timing.",
        },
    ],
    "food": [
        {
            "name": "Orders, Catering & Event Inquiries",
            "description": "Tell us your date, guest count, and menu questions.",
        },
    ],
    "wellness": [
        {
            "name": "Appointments & New Client Inquiries",
            "description": "Share what you are looking for and your preferred days.",
        },
    ],
    "other": [
        {
            "name": "Service Inquiry & Scope Discussion",
            "description": "Tell us what you have in mind and how to reach you.",
        },
    ],
}

# Disallow unverified price bands, dollar amounts, hourly rate claims, or permit/rank promises
_UNVERIFIED_CLAIM_RE = re.compile(
    r"(\$\s*\d+|\b\d+\s*(?:usd|dollars?)\b|\b\d+\s*(?:hrs?|hours?)\s*(?:of\s+labor|estimate)\b"
    r"|\bguaranteed\s+#?1\b|\bpage\s+1\s+on\s+google\b|\bno\s+permit\s+required\b)",
    re.IGNORECASE,
)

STATIC_SCAN_DISCLAIMER = (
    "This card reports only what was observed in the initial static HTML response without "
    "executing JavaScript, logging in, or loading external scripts. It does not measure "
    "website traffic, search rankings, page speed, or inquiry conversion rates, and does "
    "not verify business ownership, licensing, or insurance."
)


def _strip_ctrl(value: str, limit: int) -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def normalize_industry(raw: str | None) -> str:
    if not raw or not isinstance(raw, str):
        return "other"
    key = re.sub(r"[\s\-]+", "_", raw.strip().lower())
    mapped = INDUSTRY_ALIASES.get(key, key)
    return mapped if mapped in sitegen.INDUSTRIES else "other"


def normalize_color(raw: str | None, *, strict: bool = False) -> str:
    if not raw or not isinstance(raw, str):
        return "ocean"
    key = raw.strip().lower()
    if key in sitegen.COLORS:
        return key
    if strict:
        raise sitegen.ProfileError("Choose a supported color.")
    return "ocean"


def normalize_response_window(raw: str | None, *, strict: bool = False) -> str:
    if not raw or not isinstance(raw, str):
        return "no_promise"
    key = re.sub(r"[\s\-]+", "_", raw.strip().lower())
    mapped = RESPONSE_WINDOW_ALIASES.get(key, key)
    if mapped in sitegen.RESPONSE_WINDOWS:
        return mapped
    if strict:
        raise sitegen.ProfileError("Choose a supported response window.")
    return "no_promise"


class DiagnosticFinding(BaseModel):
    """A single evidence-backed finding from static HTML inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=2, max_length=60)
    title: str = Field(min_length=3, max_length=160)
    status: ObservationStatus
    evidence_status: EvidenceStatus
    observed_detail: str = Field(min_length=3, max_length=500)
    capability: str = Field(default="website", max_length=40)
    what_we_do: str = Field(min_length=3, max_length=400)
    first_step: str = Field(min_length=3, max_length=300)
    evidence_needed: str = Field(min_length=3, max_length=400)
    improvement_metric: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _validate_provenance(self) -> DiagnosticFinding:
        if self.evidence_status == "owner_stated":
            raise ValueError("Scanner findings must never be labeled owner_stated.")
        if self.status == "unable_to_determine" and self.evidence_status != "unknown":
            raise ValueError("Unable-to-determine findings must have evidence_status='unknown'.")
        if self.status in ("observed", "not_observed") and self.evidence_status != "system_recorded":
            raise ValueError("Completed static HTML observations must have evidence_status='system_recorded'.")
        if self.capability not in assessment.CAPABILITY_NAMES:
            raise ValueError(f"Unsupported capability key: {self.capability}")
        return self


class DiagnosticObservationCard(BaseModel):
    """Complete tri-state static HTML diagnostic result for a public URL."""

    model_config = ConfigDict(extra="forbid")

    input_url: str = Field(min_length=1, max_length=500)
    final_url: str | None = Field(default=None, max_length=500)
    pinned_ip: str | None = Field(default=None, max_length=64)
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scan_state: ScanState
    http_status: int | None = None
    content_type: str | None = Field(default=None, max_length=120)
    failure_reason: str | None = Field(default=None, max_length=300)

    title_status: ObservationStatus = "unable_to_determine"
    observed_title: str = Field(default="", max_length=200)

    h1_status: ObservationStatus = "unable_to_determine"
    observed_h1: str = Field(default="", max_length=200)

    tap_to_call_status: ObservationStatus = "unable_to_determine"
    observed_tel_links: list[str] = Field(default_factory=list)

    mailto_status: ObservationStatus = "unable_to_determine"
    observed_mailto_links: list[str] = Field(default_factory=list)

    contact_form_status: ObservationStatus = "unable_to_determine"
    observed_form_fields: list[str] = Field(default_factory=list)

    viewport_meta_status: ObservationStatus = "unable_to_determine"
    observed_viewport_meta: str = Field(default="", max_length=200)

    cta_status: ObservationStatus = "unable_to_determine"
    observed_cta_texts: list[str] = Field(default_factory=list)

    findings: list[DiagnosticFinding] = Field(default_factory=list)
    unknowns: list[str] = Field(
        default_factory=lambda: [
            "Elements rendered only by client-side JavaScript after page load",
            "Actual visitor traffic, search impressions, clicks, and inquiry conversion rates",
            "How quickly phone calls or inquiries receive a response today",
            "Verified business ownership, licensing, or insurance status",
        ]
    )
    disclaimer: str = Field(default=STATIC_SCAN_DISCLAIMER, max_length=600)

    @model_validator(mode="after")
    def _validate_non_completed_state(self) -> DiagnosticObservationCard:
        if self.scan_state != "completed":
            status_fields = (
                self.title_status,
                self.h1_status,
                self.tap_to_call_status,
                self.mailto_status,
                self.contact_form_status,
                self.viewport_meta_status,
                self.cta_status,
            )
            if any(s != "unable_to_determine" for s in status_fields):
                raise ValueError(
                    "When scan_state is not 'completed', all check statuses must be 'unable_to_determine'."
                )
            if any(f.status != "unable_to_determine" for f in self.findings):
                raise ValueError(
                    "Non-completed scans must never emit observed or not_observed findings."
                )
        return self


class WidgetParameterOption(BaseModel):
    """Single deterministic choice within a Scope & Readiness Explorer parameter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_\-]{0,59}$")
    label: str = Field(min_length=1, max_length=100)
    scope_note: str = Field(min_length=1, max_length=200)

    @field_validator("label", "scope_note")
    @classmethod
    def _reject_unverified_claims(cls, v: str) -> str:
        cleaned = _strip_ctrl(v, 200)
        if _UNVERIFIED_CLAIM_RE.search(cleaned):
            raise ValueError(
                "Unverified pricing, labor-hour estimates, permit claims, or ranking guarantees are forbidden in demo widgets."
            )
        return cleaned


class WidgetParameter(BaseModel):
    """One interactive dimension (e.g., project type, property readiness, timeline)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=2, max_length=90)
    help_text: str = Field(default="", max_length=180)
    options: list[WidgetParameterOption] = Field(min_length=2, max_length=6)

    @field_validator("label", "help_text")
    @classmethod
    def _clean_and_check(cls, v: str) -> str:
        cleaned = _strip_ctrl(v, 180)
        if _UNVERIFIED_CLAIM_RE.search(cleaned):
            raise ValueError("Unverified pricing or guarantees are forbidden in widget parameters.")
        return cleaned


class WidgetSpec(BaseModel):
    """Deterministic, accessible Scope & Readiness Explorer specification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    widget_id: str = Field(
        default="scope_readiness_explorer",
        pattern=r"^[a-z][a-z0-9_]{1,49}$",
    )
    version: Literal["1.0"] = "1.0"
    industry: str = Field(default="other")
    heading: str = Field(min_length=3, max_length=90)
    intro: str = Field(min_length=3, max_length=240)
    parameters: list[WidgetParameter] = Field(min_length=2, max_length=4)
    summary_heading: str = Field(
        default="Structured Scope Summary (Preview Only)",
        min_length=3,
        max_length=90,
    )
    disclaimer: str = Field(
        default=(
            "This explorer organizes project details for an owner review. It never estimates "
            "prices, labor hours, code compliance, or permit requirements."
        ),
        min_length=10,
        max_length=300,
    )
    owner_verified_pricing: Literal[False] = False

    @field_validator("industry")
    @classmethod
    def _norm_industry(cls, v: str) -> str:
        return normalize_industry(v)

    @field_validator("heading", "intro", "summary_heading", "disclaimer")
    @classmethod
    def _reject_claims(cls, v: str) -> str:
        cleaned = _strip_ctrl(v, 300)
        if _UNVERIFIED_CLAIM_RE.search(cleaned):
            raise ValueError("Unverified pricing or guarantees are forbidden in widget copy.")
        return cleaned


class PreviewConceptProfile(BaseModel):
    """Partial, unclaimed concept profile for safe preview rendering.

    Unlike ``sitegen.validate_profile``, this model permits unknown ``city``, ``phone``,
    or ``email`` during anonymous public previews while explicitly recording them in
    ``unconfirmed_fields`` so they are never fabricated.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Local Business Concept", min_length=1, max_length=90)
    city: str = Field(default="", max_length=100)
    industry: str = Field(default="other", max_length=40)
    color: str = Field(default="ocean", max_length=20)
    email: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)
    about: str = Field(default="", max_length=800)
    headline: str = Field(default="", max_length=90)
    intro: str = Field(default="", max_length=240)
    response_window: str = Field(default="no_promise", max_length=30)
    services: list[dict[str, str]] = Field(default_factory=list)
    structured_intake: bool = True
    widgets: list[WidgetSpec] = Field(default_factory=list)
    unconfirmed_fields: list[str] = Field(default_factory=list)
    is_concept_only: Literal[True] = True

    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if "business_name" in out and "name" not in out:
            out["name"] = out.pop("business_name")
        out["name"] = _strip_ctrl(out.get("name") or "Local Business Concept", 90) or "Local Business Concept"
        out["city"] = _strip_ctrl(out.get("city", ""), 100)
        out["industry"] = normalize_industry(out.get("industry"))
        out["color"] = normalize_color(out.get("color"), strict=False)
        out["response_window"] = normalize_response_window(out.get("response_window"), strict=False)
        out["email"] = _strip_ctrl(out.get("email", ""), 120)
        out["phone"] = _strip_ctrl(out.get("phone", ""), 40)
        out["about"] = _strip_ctrl(out.get("about", ""), 800)
        out["headline"] = _strip_ctrl(out.get("headline", ""), 90)
        out["intro"] = _strip_ctrl(out.get("intro", ""), 240)

        for field_name in ("about", "headline", "intro"):
            if _UNVERIFIED_CLAIM_RE.search(out[field_name]):
                raise ValueError(f"Unverified pricing or rank claim rejected in {field_name}.")

        unconfirmed: list[str] = list(out.get("unconfirmed_fields") or [])
        if not out["city"] and "city" not in unconfirmed:
            unconfirmed.append("city")
        if not out["phone"] and not out["email"] and "contact_method" not in unconfirmed:
            unconfirmed.append("contact_method")

        raw_services = out.get("services")
        if raw_services:
            parsed_services = sitegen.parse_services(raw_services)
        else:
            parsed_services = [
                dict(item) for item in DEFAULT_SERVICES_BY_INDUSTRY.get(out["industry"], DEFAULT_SERVICES_BY_INDUSTRY["other"])
            ]
            if "services" not in unconfirmed:
                unconfirmed.append("services")
        for svc in parsed_services:
            if _UNVERIFIED_CLAIM_RE.search(svc.get("name", "")) or _UNVERIFIED_CLAIM_RE.search(svc.get("description", "")):
                raise ValueError("Unverified pricing or rank claim rejected in service description.")
        out["services"] = parsed_services
        out["unconfirmed_fields"] = unconfirmed
        return out


def adapt_to_sitegen_profile(
    raw_owner_input: dict[str, Any] | PreviewConceptProfile,
    *,
    include_widgets: bool = False,
) -> dict[str, Any]:
    """Normalize owner-confirmed input into a canonical ``sitegen.validate_profile`` dict.

    Enforces:
    - Industry mapping (``remodeling`` -> ``construction``, ``professional_services`` ->
      ``professional``, ``general`` -> ``other``) into ``sitegen.INDUSTRIES``.
    - Named color key validation against ``sitegen.COLORS`` (rejects raw hex strings or
      unknown palette names with ``sitegen.ProfileError``).
    - Response window validation against ``sitegen.RESPONSE_WINDOWS``.
    - Non-empty ``name``, ``city``, 1..8 normalized ``services``, and at least one of
      ``phone`` or ``email`` via ``sitegen.validate_profile()``.
    """
    if isinstance(raw_owner_input, PreviewConceptProfile):
        source: dict[str, Any] = raw_owner_input.model_dump()
    elif isinstance(raw_owner_input, dict):
        source = dict(raw_owner_input)
    else:
        raise sitegen.ProfileError("Expected website details.")

    name_val = source.get("name") or source.get("business_name") or ""
    raw_industry = source.get("industry")
    if isinstance(raw_industry, str) and raw_industry.strip():
        ind_key = re.sub(r"[\s\-]+", "_", raw_industry.strip().lower())
        industry_val = INDUSTRY_ALIASES.get(ind_key, ind_key)
    else:
        industry_val = "other"

    raw_color = source.get("color")
    color_val = normalize_color(raw_color, strict=True) if raw_color else "ocean"

    raw_window = source.get("response_window")
    window_val = normalize_response_window(raw_window, strict=True) if raw_window else "no_promise"

    candidate = {
        "name": _strip_ctrl(name_val, 90) if isinstance(name_val, str) else name_val,
        "city": _strip_ctrl(source.get("city", ""), 100) if isinstance(source.get("city", ""), str) else source.get("city"),
        "industry": industry_val,
        "color": color_val,
        "email": _strip_ctrl(source.get("email", ""), 120) if isinstance(source.get("email", ""), str) else source.get("email"),
        "phone": _strip_ctrl(source.get("phone", ""), 40) if isinstance(source.get("phone", ""), str) else source.get("phone"),
        "about": _strip_ctrl(source.get("about", ""), 800) if isinstance(source.get("about", ""), str) else source.get("about"),
        "headline": _strip_ctrl(source.get("headline", ""), 90) if isinstance(source.get("headline", ""), str) else source.get("headline"),
        "intro": _strip_ctrl(source.get("intro", ""), 240) if isinstance(source.get("intro", ""), str) else source.get("intro"),
        "response_window": window_val,
        "services": source.get("services", ""),
        "structured_intake": source.get("structured_intake", True),
    }

    validated = sitegen.validate_profile(candidate)
    if include_widgets and "widgets" in source and source["widgets"] is not None:
        validated_widgets = [
            w.model_dump() if isinstance(w, WidgetSpec) else WidgetSpec.model_validate(w).model_dump()
            for w in source["widgets"]
        ]
        validated["widgets"] = validated_widgets
    return validated


def build_assessment_result_from_preview(
    card: DiagnosticObservationCard | None,
    entry_path: EntryPath = "existing_site",
    business_name: str = "",
) -> dict[str, Any]:
    """Build an ``Assessment.result`` payload compatible with ``orders.provision()`` and
    ``assessment.improvements_from()`` while preserving truthful evidence provenance.

    Rules enforced:
    - Never labels static scanner findings or path selections as ``owner_stated``.
    - If ``card`` is unreachable, HTTP error (e.g. 503), or non-HTML, produces ZERO
      ``not_observed`` obstacles and sets ``understood=False``, ``recommended_offer='consult'``.
    - If ``card`` completed and all checks are ``observed``, produces ZERO obstacles
      (never invents a fallback website-launch obstacle).
    """
    if entry_path not in ENTRY_PATHS:
        raise ValueError(f"Unsupported entry_path: {entry_path}")

    clean_business = _strip_ctrl(business_name, 120) or "the business"
    obstacles: list[dict[str, Any]] = []
    next_steps: list[str] = []
    unknowns: list[str] = [
        "How many inquiries you receive and how quickly they get a reply",
        "Where your current customers come from",
        "Website traffic and search performance (no analytics or Search Console access has been granted)",
        "Revenue, profit and return on any change. We do not estimate these",
    ]

    if entry_path == "existing_site":
        if card is None or card.scan_state != "completed":
            reason_detail = (
                card.failure_reason
                if card and card.failure_reason
                else "The website could not be reached or did not return an inspectable HTML page."
            )
            unknowns.insert(0, f"Current website HTML contents ({reason_detail})")
            return {
                "engine": assessment.ENGINE_VERSION,
                "entry_path": entry_path,
                "method": (
                    "Bounded static HTML inspection attempted. Because the page could not be "
                    "verified, no missing-feature findings were inferred."
                ),
                "headline": (
                    f"We could not verify the current website for {clean_business}, so we did "
                    "not assume any features are missing."
                ),
                "understood": False,
                "needs_human_review": True,
                "priorities": [],
                "obstacles": [],
                "next_steps": [
                    "Confirm the public website address or share what you want to improve in a short working session.",
                ],
                "recommended_offer": "consult",
                "recommendation_reason": (
                    "Because the public URL could not be inspected deterministically, we recommend "
                    "confirming your current setup before proposing changes."
                ),
                "unknowns": unknowns,
                "out_of_scope": [],
                "disclaimer": STATIC_SCAN_DISCLAIMER,
            }

        missing_findings = [f for f in card.findings if f.status == "not_observed"]
        for idx, finding in enumerate(missing_findings[:4]):
            obstacles.append(
                {
                    "code": finding.code,
                    "label": finding.title,
                    "in_their_words": "",
                    "observed_detail": finding.observed_detail,
                    "evidence_status": "system_recorded",
                    "evidence_label": "Observed in static HTML scan (non-JS). Confirm with owner before changes.",
                    "evidence_needed": finding.evidence_needed,
                    "capability": finding.capability,
                    "capability_name": assessment.CAPABILITY_NAMES[finding.capability],
                    "what_we_do": finding.what_we_do,
                    "first_step": finding.first_step,
                    "in_scope": True,
                    "metric": finding.improvement_metric,
                    "match_strength": max(1, 4 - idx),
                }
            )

        unknowns = list(card.unknowns)
        if not obstacles:
            # All inspected HTML signals were observed — never fabricate a missing-website obstacle!
            return {
                "engine": assessment.ENGINE_VERSION,
                "entry_path": entry_path,
                "method": "Bounded static HTML inspection (no JavaScript execution, no LLM, no external data).",
                "headline": (
                    f"Core mobile, heading, and contact signals were observed in static HTML for {clean_business}."
                ),
                "understood": True,
                "needs_human_review": False,
                "priorities": [],
                "obstacles": [],
                "next_steps": [
                    "Review whether incoming inquiries and follow-ups need a shared workspace or structured scope explorer.",
                ],
                "recommended_offer": "consult",
                "recommendation_reason": (
                    "Static HTML already exposes key contact and mobile elements. A conversation "
                    "can confirm whether operational follow-up or proposal tools would be useful."
                ),
                "unknowns": unknowns,
                "out_of_scope": [],
                "disclaimer": card.disclaimer,
            }

        next_steps = [o["first_step"] for o in obstacles[:3]]
        return {
            "engine": assessment.ENGINE_VERSION,
            "entry_path": entry_path,
            "method": "Bounded static HTML inspection (no JavaScript execution, no LLM, no external data).",
            "headline": f"Static HTML observations and candidate improvements for {clean_business}.",
            "understood": True,
            "needs_human_review": False,
            "priorities": [],
            "obstacles": obstacles,
            "next_steps": next_steps,
            "recommended_offer": "launch",
            "recommendation_reason": (
                "One or more customer-contact or clarity signals were not observed in static HTML. "
                "Confirming and publishing an updated site or intake flow addresses these gaps."
            ),
            "unknowns": unknowns,
            "out_of_scope": [],
            "disclaimer": card.disclaimer,
        }

    if entry_path == "new_business":
        obstacles.append(
            {
                "code": "web_presence",
                "label": "Launch an initial mobile-friendly website with structured inquiry capture",
                "in_their_words": "",
                "observed_detail": "Visitor selected the new-business / no-existing-website preview path.",
                "evidence_status": "system_recorded",
                "evidence_label": "Recorded from new-business preview selection (no existing URL scanned).",
                "evidence_needed": (
                    "Whether visitors reach the site and whether they start or finish the contact form. "
                    "Measured only after the site is approved and published."
                ),
                "capability": "website",
                "capability_name": assessment.CAPABILITY_NAMES["website"],
                "what_we_do": (
                    "Build a clear, mobile-friendly site that says what you do and where, with an "
                    "inquiry form that feeds your workspace. You approve it before it is published."
                ),
                "first_step": "Confirm your services, service area, and contact details, then review your draft site.",
                "in_scope": True,
                "metric": "Page views, contact-form starts and completed inquiries on the published site",
                "match_strength": 1,
            }
        )
        return {
            "engine": assessment.ENGINE_VERSION,
            "entry_path": entry_path,
            "method": "Rule-based new-business preview configuration. No outside data or AI model was used.",
            "headline": f"Initial website and inquiry workspace plan for {clean_business}.",
            "understood": True,
            "needs_human_review": False,
            "priorities": [],
            "obstacles": obstacles,
            "next_steps": [obstacles[0]["first_step"]],
            "recommended_offer": "launch",
            "recommendation_reason": (
                "Launching an owner-approved website with a structured inquiry form establishes "
                "your public presence and private inquiry desk together."
            ),
            "unknowns": unknowns,
            "out_of_scope": [],
            "disclaimer": STATIC_SCAN_DISCLAIMER,
        }

    # operations_only or legacy_assessment without free-text
    obstacles.append(
        {
            "code": "follow_up",
            "label": "Organize incoming inquiries, follow-up dates, and itemized proposals in one workspace",
            "in_their_words": "",
            "observed_detail": f"Visitor selected the {entry_path} path.",
            "evidence_status": "system_recorded",
            "evidence_label": "Recorded from workspace preview path selection.",
            "evidence_needed": (
                "How many inquiries arrive each week and how long each waits for a first reply. "
                "Measured once inquiries flow through the workspace."
            ),
            "capability": "inquiry_desk",
            "capability_name": assessment.CAPABILITY_NAMES["inquiry_desk"],
            "what_we_do": (
                "Put every inquiry in one private list with a status, an owner and a follow-up date, "
                "so nothing depends on memory."
            ),
            "first_step": "Set up your inquiry desk and log or route incoming customer requests.",
            "in_scope": True,
            "metric": "Share of inquiries with a recorded first response, and days to first response",
            "match_strength": 1,
        }
    )
    return {
        "engine": assessment.ENGINE_VERSION,
        "entry_path": entry_path,
        "method": "Rule-based operational preview configuration. No outside data or AI model was used.",
        "headline": f"Inquiry desk and follow-up plan for {clean_business}.",
        "understood": True,
        "needs_human_review": False,
        "priorities": [],
        "obstacles": obstacles,
        "next_steps": [obstacles[0]["first_step"]],
        "recommended_offer": "launch",
        "recommendation_reason": "A shared inquiry desk and proposal workflow keeps follow-up organized.",
        "unknowns": unknowns,
        "out_of_scope": [],
        "disclaimer": STATIC_SCAN_DISCLAIMER,
    }


def improvements_from_preview_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn in-scope obstacles into ``Improvement`` rows while preserving ``evidence_status``."""
    items: list[dict[str, Any]] = []
    for o in result.get("obstacles", []):
        if not o.get("in_scope"):
            continue
        ev_status = o.get("evidence_status")
        if ev_status not in EVIDENCE_STATUSES:
            ev_status = "system_recorded" if result.get("entry_path") in ("existing_site", "new_business") else "owner_stated"
        items.append(
            {
                "title": o["capability_name"] + ": " + o["label"],
                "detail": o["what_we_do"],
                "capability": o["capability"],
                "metric": o.get("metric", ""),
                "evidence_status": ev_status,
                "evidence_note": o["evidence_needed"],
            }
        )
    return items
