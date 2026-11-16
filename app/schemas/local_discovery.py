"""Provider-neutral local discovery contracts (no provider execution).

A planned search cell is not proof of a Google/Maps rank. Only a real, trusted
SERP provider observation can carry a specific organic/local-pack position.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from urllib.parse import urlsplit
import ipaddress
import re

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class SearchSource(str, Enum):
    PLACES = "places_business_search"
    ORGANIC_SERP = "organic_serp"
    LOCAL_PACK_SERP = "local_pack_serp"
    SEARCH_CONSOLE = "search_console"


class EvidenceClass(str, Enum):
    VERIFIED_PROVIDER = "verified_provider_receipt"
    OWNER_IMPORT = "owner_imported"
    SYNTHETIC = "synthetic_fixture"


class Presence(str, Enum):
    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    UNKNOWN = "unknown"


IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
SHA256_HEX = r"^[a-f0-9]{64}$"


class GeoMarket(ImmutableModel):
    id: str = Field(pattern=IDENTIFIER)
    city: str = Field(min_length=2, max_length=100)
    region: str = Field(min_length=2, max_length=100)
    country: str = Field(min_length=2, max_length=100)


class CoordinateAnchor(ImmutableModel):
    id: str = Field(pattern=IDENTIFIER)
    market_id: str = Field(pattern=IDENTIFIER)
    label: str = Field(min_length=1, max_length=100)
    latitude: FiniteFloat = Field(ge=-90, le=90)
    longitude: FiniteFloat = Field(ge=-180, le=180)
    radius_m: int = Field(ge=50, le=50000)


class SearchPhrase(ImmutableModel):
    id: str = Field(pattern=IDENTIFIER)
    base_text: str = Field(min_length=2, max_length=160)
    variants: tuple[str, ...] = Field(min_length=1, max_length=12)

    @field_validator("base_text")
    @classmethod
    def valid_base_text(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("Search phrase must be printable text")
        return value.strip()

    @field_validator("variants")
    @classmethod
    def valid_variants(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not text.strip() or len(text.strip()) > 160 or
               any(ord(char) < 32 for char in text) for text in values):
            raise ValueError("Search variants must be nonempty printable text up to 160 chars")
        if len({text.casefold().strip() for text in values}) != len(values):
            raise ValueError("Search phrase variants must be unique")
        return tuple(text.strip() for text in values)


class ProviderProfile(ImmutableModel):
    id: str = Field(pattern=IDENTIFIER)
    source: SearchSource
    localization: Literal["coordinate_radius", "market_name", "none"]
    supports_device: bool = False
    supports_locale: bool = False
    # Describes a verified integration profile, not a claim about all providers.

    @model_validator(mode="after")
    def validate_location_claim(self) -> "ProviderProfile":
        if self.source == SearchSource.SEARCH_CONSOLE and self.localization != "none":
            raise ValueError("Search Console property reports cannot claim map-coordinate localization")
        return self


class MatrixInputs(ImmutableModel):
    markets: tuple[GeoMarket, ...] = Field(min_length=1, max_length=6)
    anchors: tuple[CoordinateAnchor, ...] = Field(min_length=1, max_length=18)
    phrases: tuple[SearchPhrase, ...] = Field(min_length=1, max_length=20)
    providers: tuple[ProviderProfile, ...] = Field(min_length=1, max_length=6)
    requested_device: Literal["mobile", "desktop"] | None = None
    requested_locale: str | None = Field(default=None, min_length=2, max_length=24)
    max_cells: int = Field(default=160, ge=1, le=1000)
    max_unique_requests: int = Field(default=160, ge=1, le=1000)
    # Owner approval for spend and execution belongs to a separate future API.

    @model_validator(mode="after")
    def validate_ids_and_membership(self) -> "MatrixInputs":
        for name, values in (
            ("market", self.markets), ("anchor", self.anchors),
            ("phrase", self.phrases), ("provider", self.providers),
        ):
            ids = [item.id for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate {name} IDs")
        markets = {market.id for market in self.markets}
        covered = {anchor.market_id for anchor in self.anchors}
        if not covered.issubset(markets):
            raise ValueError("Each anchor must belong to a selected market")
        if markets != covered:
            raise ValueError("Every selected market requires at least one coordinate anchor")
        return self


class MatrixCell(ImmutableModel):
    id: str = Field(pattern=r"^[a-f0-9]{20}$")
    market_id: str = Field(pattern=IDENTIFIER)
    anchor_id: str = Field(pattern=IDENTIFIER)
    query_id: str = Field(pattern=IDENTIFIER)
    phrase: str = Field(min_length=1, max_length=160)
    provider_id: str = Field(pattern=IDENTIFIER)
    source: SearchSource
    # Exact parameters sent to provider, as canonical immutable JSON text.
    request_json: str
    request_signature: str = Field(pattern=SHA256_HEX)
    coordinate_parameter_applied: bool
    device_parameter_applied: bool
    locale_parameter_applied: bool
    status: Literal["planned_not_executed"] = "planned_not_executed"


class MatrixPlan(ImmutableModel):
    cells: tuple[MatrixCell, ...]
    logical_cells: int
    distinct_provider_requests: int
    unsupported_coordinate_cells: int
    unsupported_device_cells: int
    unsupported_locale_cells: int
    execution_performed: Literal[False] = False


class SourceObservation(ImmutableModel):
    """Evidence from an already-executed, separately trusted provider adapter."""

    cell_id: str = Field(pattern=r"^[a-f0-9]{20}$")
    request_signature: str = Field(pattern=SHA256_HEX)
    provider_id: str = Field(pattern=IDENTIFIER)
    source: SearchSource
    observed_at: datetime
    provider_receipt_id: str = Field(min_length=1, max_length=200)
    evidence_class: EvidenceClass
    presence: Presence
    rank_position: int | None = Field(default=None, ge=1)
    gsc_average_position: FiniteFloat | None = Field(default=None, ge=1)
    # No absence means "not observed" unless a successful, scoped source confirms it.

    @model_validator(mode="after")
    def enforce_provenance(self) -> "SourceObservation":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Evidence timestamps must have an explicit timezone")
        if self.rank_position is not None:
            if self.source not in {SearchSource.ORGANIC_SERP, SearchSource.LOCAL_PACK_SERP}:
                raise ValueError("Only a real organic/local-pack SERP source may carry rank")
            if self.evidence_class != EvidenceClass.VERIFIED_PROVIDER:
                raise ValueError("Synthetic or owner-imported ranking is not verified")
            if self.presence != Presence.OBSERVED:
                raise ValueError("Unknown or absent results cannot have a position")
        if self.gsc_average_position is not None:
            if self.source != SearchSource.SEARCH_CONSOLE:
                raise ValueError("GSC average position is not a universal ranking metric")
            if self.presence != Presence.OBSERVED:
                raise ValueError("Only observed GSC records can have an average position")
            if self.evidence_class == EvidenceClass.SYNTHETIC:
                raise ValueError("Synthetic GSC sample is not measured source evidence")
            # Owner-imported real Search Console CSV is valid source-labeled evidence;
            # it is NOT automatically a verified provider receipt.

        if self.presence == Presence.NOT_OBSERVED and self.evidence_class != EvidenceClass.VERIFIED_PROVIDER:
            raise ValueError("Unverified sources cannot establish absence")
        return self


def normalize_website_domain(raw: str | None) -> str | None:
    """Strict host-level identity key; never fetch a URL or collapse directory pages."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("Website domain must be text")
    candidate = raw.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        port = parts.port  # Forces malformed ports and IPv6 text to be validated.
    except ValueError as exc:
        raise ValueError("Invalid website domain") from exc
    if (parts.scheme.lower() not in ("http", "https") or not host
            or parts.username or parts.password or port is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("Provide a public website hostname, not a path or login URL")
    try:
        domain = host.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise ValueError("Invalid international website hostname") from exc
    if domain.startswith("www."):
        domain = domain[4:]
    if len(domain) > 253:
        raise ValueError("Website hostname is too long")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError("Public business identity requires a domain, not an IP address")
    labels = domain.split(".")
    label_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if (len(labels) < 2 or not all(label_pattern.fullmatch(p) for p in labels)
            or not re.fullmatch(r"(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})", labels[-1])):
        raise ValueError("Website domain labels are invalid")
    return domain


class BusinessCandidate(ImmutableModel):
    id: str = Field(pattern=IDENTIFIER)
    provider_id: str = Field(pattern=IDENTIFIER)
    name: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=100)
    provider_place_id: str | None = Field(default=None, min_length=1, max_length=200)
    website_domain: str | None = Field(default=None, min_length=4, max_length=253)
    domain_verified: bool = False
    receipt_id: str = Field(min_length=1, max_length=200)

    @field_validator("website_domain")
    @classmethod
    def checked_website_domain(cls, value: str | None) -> str | None:
        return normalize_website_domain(value)

    @model_validator(mode="after")
    def domain_confirmation(self) -> "BusinessCandidate":
        if self.domain_verified and not self.website_domain:
            raise ValueError("A verified domain must have a source-reported website")
        return self


class AliasReview(ImmutableModel):
    left_id: str = Field(pattern=IDENTIFIER)
    right_id: str = Field(pattern=IDENTIFIER)
    approved: bool
    reviewed_by: str = Field(min_length=1, max_length=80)
    evidence_ref: str = Field(min_length=1, max_length=200)


class IdentityResolution(ImmutableModel):
    clusters: tuple[tuple[str, ...], ...]
    manual_review_pairs: tuple[tuple[str, str, str], ...]
    blocked_conflicts: tuple[tuple[str, str, str], ...] = ()
    owner_reviewed_merges: int
    automatic_merges: int
