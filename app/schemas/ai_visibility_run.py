"""AI Visibility research contracts; local-only, no provider calls or asserted live ranks.

Owner-facing records distinguish verified API receipts from imported/reconstructed,
historical and synthetic material. "Verified" in incoming data is only a claim:
trust is granted exclusively by a separate server-side receipt verifier.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from urllib.parse import urlsplit

from pydantic import (
    BaseModel, ConfigDict, Field, field_validator, model_validator,
)

from app.schemas.local_discovery import normalize_website_domain

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
SHA256 = r"^[a-f0-9]{64}$"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class ProviderFamily(str, Enum):
    OPENAI_WEB_SEARCH = "openai_web_search"
    ANTHROPIC_WEB_SEARCH = "anthropic_web_search"
    GEMINI_GOOGLE_SEARCH = "gemini_google_search"
    CUSTOM_ATTESTED = "custom_attested_provider"


class LocationSupport(str, Enum):
    NONE = "none"
    APPROXIMATE_MARKET = "approximate_market"
    PROMPT_CONTEXT_ONLY = "prompt_context_only"


class EvidenceOrigin(str, Enum):
    VERIFIED_PROVIDER = "verified_provider_receipt"
    OWNER_IMPORT = "owner_import"
    HISTORICAL = "historical_record"
    RECONSTRUCTED = "reconstructed"
    SYNTHETIC = "synthetic_sample"


class AnswerState(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Market( FrozenModel):
    id: str = Field(pattern=IDENTIFIER)
    city: str = Field(min_length=2, max_length=100)
    region: str = Field(min_length=2, max_length=100)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    timezone: str | None = Field(default=None, min_length=3, max_length=80)


class Question(FrozenModel):
    id: str = Field(pattern=IDENTIFIER)
    wording: str = Field(min_length=5, max_length=500)

    @field_validator("wording")
    @classmethod
    def printable_wording(cls, text: str) -> str:
        if any(ord(char) < 32 for char in text):
            raise ValueError("AI visibility questions cannot contain ASCII control characters")
        return text


class ProviderProfile(FrozenModel):
    id: str = Field(pattern=IDENTIFIER)
    family: ProviderFamily
    model: str = Field(min_length=2, max_length=100)
    location_support: LocationSupport
    # A conservative owner-entered ceiling, not an independently verified tariff.
    max_charge_per_run_usd: Decimal = Field(gt=0, le=100, max_digits=8, decimal_places=4)
    max_web_search_uses: int = Field(ge=1, le=20)
    citation_signal: bool = True
    cost_basis: str = "owner_supplied_upper_estimate"

    @model_validator(mode="after")
    def provider_honesty(self) -> "ProviderProfile":
        if self.cost_basis != "owner_supplied_upper_estimate":
            raise ValueError("This pilot accepts only explicitly estimated owner ceilings")
        if self.family in {ProviderFamily.OPENAI_WEB_SEARCH,
                           ProviderFamily.ANTHROPIC_WEB_SEARCH}:
            if self.location_support not in {
                LocationSupport.NONE, LocationSupport.APPROXIMATE_MARKET,
            }:
                raise ValueError("OpenAI/Anthropic web search cannot claim GPS impersonation")
        elif self.family == ProviderFamily.GEMINI_GOOGLE_SEARCH:
            if self.location_support not in {
                LocationSupport.NONE, LocationSupport.PROMPT_CONTEXT_ONLY,
            }:
                raise ValueError("Google Search grounding cannot be claimed as a GPS API")
        elif self.family == ProviderFamily.CUSTOM_ATTESTED:
            # Coordinate-precise AI web answer localization is not verified here.
            if self.location_support == LocationSupport.APPROXIMATE_MARKET:
                raise ValueError("Custom location support needs a separately reviewed adapter")
        return self


class ExperimentSpec(FrozenModel):
    experiment_id: str = Field(pattern=IDENTIFIER)
    brand_name: str = Field(min_length=2, max_length=160)
    brand_domain: str = Field(min_length=4, max_length=253)
    markets: tuple[Market, ...] = Field(min_length=1, max_length=8)
    questions: tuple[Question, ...] = Field(min_length=1, max_length=30)
    providers: tuple[ProviderProfile, ...] = Field(min_length=1, max_length=6)
    repetitions: int = Field(default=1, ge=1, le=8)
    max_planned_runs: int = Field(default=80, ge=1, le=500)
    max_estimated_total_usd: Decimal = Field(
        default=Decimal("5.00"), gt=0, le=500, max_digits=8, decimal_places=4
    )
    # There is no execution route in MF-022.
    live_execution_enabled: bool = False

    @field_validator("brand_domain")
    @classmethod
    def validated_domain(cls, value: str) -> str:
        clean = normalize_website_domain(value)
        if not clean:
            raise ValueError("A brand website domain is required")
        return clean

    @model_validator(mode="after")
    def reject_ambiguous_inputs(self) -> "ExperimentSpec":
        if self.live_execution_enabled:
            raise ValueError("This local research contract cannot execute providers")
        for name, items in (
            ("market", self.markets), ("question", self.questions),
            ("provider", self.providers),
        ):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate {name} IDs")
        return self


class PlannedAIRequest(FrozenModel):
    id: str = Field(pattern=r"^[a-f0-9]{20}$")
    experiment_id: str = Field(pattern=IDENTIFIER)
    provider_id: str = Field(pattern=IDENTIFIER)
    provider_family: ProviderFamily
    model: str
    question_id: str = Field(pattern=IDENTIFIER)
    market_id: str = Field(pattern=IDENTIFIER)
    repetition: int = Field(ge=1)
    original_question: str
    request_json: str
    request_signature: str = Field(pattern=SHA256)
    idempotency_key: str = Field(pattern=SHA256)
    location_sent_as_parameter: bool
    search_uses_ceiling: int = Field(ge=1)
    estimated_cost_ceiling_usd: Decimal = Field(ge=0)
    status: str = "planned_not_executed"

    @model_validator(mode="after")
    def unexecuted(self) -> "PlannedAIRequest":
        if self.status != "planned_not_executed":
            raise ValueError("Planned request is not execution proof")
        return self


class ExperimentPlan(FrozenModel):
    experiment_id: str
    experiment_signature: str = Field(pattern=SHA256)
    requests: tuple[PlannedAIRequest, ...]
    logical_runs: int
    max_possible_search_uses: int
    estimated_cost_ceiling_usd: Decimal
    missing_location_parameter_runs: int
    live_execution_performed: bool = False

    @model_validator(mode="after")
    def not_live(self) -> "ExperimentPlan":
        if self.live_execution_performed:
            raise ValueError("MF-022 planner cannot report live API execution")
        return self


class ProviderObservation(FrozenModel):
    request_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    request_signature: str = Field(pattern=SHA256)
    provider_id: str = Field(pattern=IDENTIFIER)
    model: str = Field(min_length=2, max_length=100)
    observed_at: datetime
    receipt_id: str = Field(min_length=1, max_length=200)
    origin: EvidenceOrigin
    state: AnswerState
    brand_mentioned: bool | None = None
    citations: tuple[str, ...] = Field(default=(), max_length=100)
    # Entire raw answer may be stored by a future encrypted/safe evidence store;
    # this contract intentionally excludes raw response contents and secrets.

    @field_validator("citations")
    @classmethod
    def valid_citation_urls(cls, urls: tuple[str, ...]) -> tuple[str, ...]:
        for url in urls:
            if len(url) > 2048 or any(ord(c) < 32 for c in url):
                raise ValueError("Citation URL must be printable and bounded")
            try:
                parts = urlsplit(url)
                port = parts.port
            except ValueError as exc:
                raise ValueError("Citation URL is malformed") from exc
            if (parts.scheme.lower() not in {"https", "http"} or
                    not parts.hostname or parts.username or parts.password or
                    port is not None):
                raise ValueError("Citation must be an http(s) source URL")
        return urls

    @model_validator(mode="after")
    def integrity_rules(self) -> "ProviderObservation":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Observation timestamp requires a timezone offset")
        if self.state != AnswerState.COMPLETED:
            if self.brand_mentioned is not None or self.citations:
                raise ValueError("Failed/unknown responses cannot assert brand or citations")
        return self


class TrustedReceipt(FrozenModel):
    """Provided only by a separately authenticated server-side response validator."""
    request_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    request_signature: str = Field(pattern=SHA256)
    provider_id: str = Field(pattern=IDENTIFIER)
    receipt_id: str = Field(min_length=1, max_length=200)
    verification_method: str = Field(pattern=r"^(provider_api_response|signed_provider_webhook)$")
    observation_sha256: str = Field(pattern=SHA256)
    verified_at: datetime

    @model_validator(mode="after")
    def aware(self) -> "TrustedReceipt":
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("Receipt verification timestamp requires timezone")
        return self


class VisibilityReport(FrozenModel):
    experiment_id: str
    planned_runs: int
    verified_completed_runs: int
    eligible_mention_runs: int
    eligible_citation_runs: int
    brand_mentions: int
    brand_domain_citation_runs: int
    mention_rate: Decimal | None
    brand_domain_citation_rate: Decimal | None
    unknown_brand_signal_runs: int
    owner_imported_records: int
    historical_records: int
    reconstructed_records: int
    synthetic_records: int
    unverified_receipt_claims: int
    incomplete_or_failed_records: int
    not_observed_or_missing_runs: int
    # Explicitly no "AI rank" or estimate of user-specific GPS position.
