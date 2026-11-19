"""Deterministic, zero-network local search matrix and competitor identity review.

This module does NOT call Google, scrape results, infer rankings from Places
order, or spend provider credits. Provider adapters must be approved separately.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Iterable

from app.schemas.local_discovery import (
    AliasReview, BusinessCandidate, EvidenceClass, IdentityResolution,
    MatrixCell, MatrixInputs, MatrixPlan, ProviderProfile, SearchSource,
    SourceObservation, normalize_website_domain,
)

MAX_IDENTITY_CANDIDATES = 250


class MatrixLimitError(ValueError):
    """Request exceeds an explicit planning ceiling; never silently truncate."""


class IdentityConflict(ValueError):
    """Merging would conflate two distinct provider-recognized locations."""


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def fingerprint(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _request_parameters(market, anchor, phrase: str, provider: ProviderProfile,
                        requested_device: str | None, requested_locale: str | None) -> dict:
    """Only include coordinates/device/locale when that provider profile supports it."""
    request = {
        "provider_id": provider.id,
        "source": provider.source.value,
        "query": phrase,
    }
    if provider.localization == "coordinate_radius":
        request["latitude"] = anchor.latitude
        request["longitude"] = anchor.longitude
        request["radius_m"] = anchor.radius_m
    elif provider.localization == "market_name":
        request["market"] = {
            "city": market.city, "region": market.region, "country": market.country,
        }
    if requested_device and provider.supports_device:
        request["device"] = requested_device
    if requested_locale and provider.supports_locale:
        request["locale"] = requested_locale
    return request


def build_search_matrix(inputs: MatrixInputs) -> MatrixPlan:
    """Build the complete logical grid or reject it, with no implicit request execution."""
    if not isinstance(inputs, MatrixInputs):
        inputs = MatrixInputs.model_validate(inputs)
    anchors = defaultdict(list)
    for anchor in inputs.anchors:
        anchors[anchor.market_id].append(anchor)
    expected = sum(
        len(anchors[market.id]) * sum(len(p.variants) for p in inputs.phrases)
        * len(inputs.providers)
        for market in inputs.markets
    )
    if expected > inputs.max_cells:
        raise MatrixLimitError(
            f"Matrix needs {expected} logical cells; max_cells={inputs.max_cells}. "
            "Reduce markets, anchors, phrases or providers; nothing was truncated."
        )
    cells: list[MatrixCell] = []
    signatures: set[str] = set()
    missing_coordinates = 0
    missing_device = 0
    missing_locale = 0
    for market in inputs.markets:
        for anchor in anchors[market.id]:
            for seed in inputs.phrases:
                for phrase in seed.variants:
                    for provider in inputs.providers:
                        request = _request_parameters(
                            market, anchor, phrase, provider,
                            inputs.requested_device, inputs.requested_locale,
                        )
                        signature = fingerprint(request)
                        signatures.add(signature)
                        logical_identity = {
                            "market_id": market.id, "anchor_id": anchor.id,
                            "query_id": seed.id, "phrase": phrase,
                            "provider_id": provider.id,
                            "requested_device": inputs.requested_device,
                            "requested_locale": inputs.requested_locale,
                        }
                        localized = provider.localization == "coordinate_radius"
                        device_applied = bool(
                            inputs.requested_device and provider.supports_device
                        )
                        locale_applied = bool(
                            inputs.requested_locale and provider.supports_locale
                        )
                        missing_coordinates += int(not localized)
                        missing_device += int(bool(inputs.requested_device)
                                              and not device_applied)
                        missing_locale += int(bool(inputs.requested_locale)
                                              and not locale_applied)
                        cells.append(MatrixCell(
                            id=fingerprint(logical_identity)[:20],
                            market_id=market.id,
                            anchor_id=anchor.id,
                            query_id=seed.id,
                            phrase=phrase,
                            provider_id=provider.id,
                            source=provider.source,
                            request_json=canonical_json(request),
                            request_signature=signature,
                            coordinate_parameter_applied=localized,
                            device_parameter_applied=device_applied,
                            locale_parameter_applied=locale_applied,
                        ))
    if len(signatures) > inputs.max_unique_requests:
        raise MatrixLimitError(
            f"Matrix needs {len(signatures)} distinct provider requests; "
            f"max_unique_requests={inputs.max_unique_requests}. Nothing was executed."
        )
    if len(cells) != expected or len({cell.id for cell in cells}) != expected:
        raise ValueError("Duplicate or missing logical matrix cells")
    return MatrixPlan(
        cells=tuple(cells), logical_cells=expected,
        distinct_provider_requests=len(signatures),
        unsupported_coordinate_cells=missing_coordinates,
        unsupported_device_cells=missing_device,
        unsupported_locale_cells=missing_locale,
    )


def validate_evidence_for_cell(cell: MatrixCell, observation: SourceObservation) -> None:
    """Reject receipts that do not correspond exactly to the planned request."""
    if cell.id != observation.cell_id:
        raise ValueError("Evidence cell ID does not match the planned query")
    if cell.request_signature != observation.request_signature:
        raise ValueError("Evidence request parameters do not match this plan")
    if cell.provider_id != observation.provider_id or cell.source != observation.source:
        raise ValueError("Evidence provider or result family does not match this plan")
    # Validation does not itself authenticate a provider's server-side receipt.
    # A separately trusted adapter must stamp verified provenance after validation.


def metric_description(source: SearchSource) -> str:
    """No ranking claim for discovery presence or aggregated GSC reporting."""
    if source == SearchSource.PLACES:
        return "Business presence in Places results; not organic or map-pack rank"
    if source == SearchSource.SEARCH_CONSOLE:
        return "Owned-property Search Console metrics; not competitor rank"
    if source == SearchSource.ORGANIC_SERP:
        return "Observed organic SERP position only with provider receipt"
    if source == SearchSource.LOCAL_PACK_SERP:
        return "Observed local-pack position only with provider receipt"
    raise ValueError("Unknown source family")


def normalize_domain(raw: str | None) -> str | None:
    """Normalized public business website identity, not fetched or owner-verified."""
    return normalize_website_domain(raw)

def _name(raw: str) -> str:
    return " ".join(raw.casefold().split())


def resolve_competitor_identities(
    candidates: Iterable[BusinessCandidate],
    reviewed_aliases: Iterable[AliasReview] = (),
) -> IdentityResolution:
    """Merge only strong identity evidence or explicit reviewed aliases.

    Same name/city alone is ambiguous. Shared brand websites can represent
    distinct locations. Different place IDs in the SAME provider cannot merge.
    """
    records = tuple(candidates)
    if len(records) > MAX_IDENTITY_CANDIDATES:
        raise MatrixLimitError("Competitor identity candidate limit is 250")
    ids = [candidate.id for candidate in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Candidate IDs must be unique")
    by_id = {candidate.id: candidate for candidate in records}
    parents = {candidate.id: candidate.id for candidate in records}
    automatic = 0
    reviewed = 0

    def find(value: str) -> str:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: str, right: str) -> bool:
        a, b = find(left), find(right)
        if a == b:
            return False
        parents[max(a, b)] = min(a, b)
        return True

    def conflict(a: BusinessCandidate, b: BusinessCandidate) -> bool:
        return (a.provider_id == b.provider_id
                and a.provider_place_id is not None
                and b.provider_place_id is not None
                and a.provider_place_id != b.provider_place_id)

    verified_domains = {
        c.id: normalize_domain(c.website_domain) if c.domain_verified else None
        for c in records
    }
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            same_place = (
                a.provider_id == b.provider_id
                and bool(a.provider_place_id)
                and a.provider_place_id == b.provider_place_id
            )
            # Domain-only identity is conservative: no known place ID on either
            # candidate, same city AND same normalized business name.
            same_domain_unplaced = (
                not a.provider_place_id and not b.provider_place_id
                and verified_domains[a.id] is not None
                and verified_domains[a.id] == verified_domains[b.id]
                and _name(a.name) == _name(b.name)
                and _name(a.city) == _name(b.city)
            )
            if (same_place or same_domain_unplaced) and union(a.id, b.id):
                automatic += 1

    rejected_pairs: set[tuple[str, str]] = set()
    for decision in reviewed_aliases:
        if decision.left_id not in by_id or decision.right_id not in by_id:
            raise ValueError("Alias decisions must refer to known candidates")
        if decision.left_id == decision.right_id:
            raise ValueError("A reviewed alias must link different records")
        if not decision.approved:
            rejected_pairs.add(tuple(sorted((decision.left_id, decision.right_id))))
            continue
        a, b = by_id[decision.left_id], by_id[decision.right_id]
        if conflict(a, b):
            raise IdentityConflict("Distinct provider place IDs cannot be merged")
        # Do not merge an entire group if any member would create a conflict.
        left_members = [x for x in records if find(x.id) == find(a.id)]
        right_members = [x for x in records if find(x.id) == find(b.id)]
        if any(conflict(x, y) for x in left_members for y in right_members):
            raise IdentityConflict("Reviewed alias conflicts with distinct locations")
        if union(a.id, b.id):
            reviewed += 1

    groups: dict[str, list[str]] = defaultdict(list)
    for candidate in records:
        groups[find(candidate.id)].append(candidate.id)
    review_pairs: list[tuple[str, str, str]] = []
    blocked_pairs: list[tuple[str, str, str]] = []
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            if find(a.id) == find(b.id):
                continue
            same_name_city = (
                _name(a.name) == _name(b.name) and _name(a.city) == _name(b.city)
            )
            shared_site = (
                verified_domains[a.id] is not None
                and verified_domains[a.id] == verified_domains[b.id]
                and _name(a.city) == _name(b.city)
            )
            if shared_site or same_name_city:
                pair = tuple(sorted((a.id, b.id)))
                if pair in rejected_pairs:
                    continue
                left_members = [x for x in records if find(x.id) == find(a.id)]
                right_members = [x for x in records if find(x.id) == find(b.id)]
                if any(conflict(x, y) for x in left_members for y in right_members):
                    blocked_pairs.append((*pair, "distinct_provider_place_ids"))
                    continue
                reason = ("shared_domain_possible_multi_location"
                          if shared_site else "name_and_city_only")
                review_pairs.append((*pair, reason))
    ordered_groups = tuple(sorted((tuple(sorted(values)) for values in groups.values())))
    return IdentityResolution(
        clusters=ordered_groups,
        manual_review_pairs=tuple(sorted(set(review_pairs))),
        blocked_conflicts=tuple(sorted(set(blocked_pairs))),
        owner_reviewed_merges=reviewed,
        automatic_merges=automatic,
    )
