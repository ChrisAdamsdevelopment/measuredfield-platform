"""Provider-neutral local AI visibility plan and verified-receipt metrics.

No API clients, credentials, background schedulers, provider charges or
automatic publishing. A future trusted adapter must authenticate actual
provider responses and create trusted receipt proofs on the server.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Iterable
from urllib.parse import urlsplit

from app.schemas.ai_visibility_run import (
    AnswerState, EvidenceOrigin, ExperimentPlan, ExperimentSpec,
    LocationSupport, PlannedAIRequest, ProviderObservation,
    TrustedReceipt, VisibilityReport,
)


class QuotaExceeded(ValueError):
    pass


class BudgetExceeded(ValueError):
    pass


class UntrustedReceipt(ValueError):
    pass


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _signature(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def observation_digest(observation: ProviderObservation) -> str:
    """Bind an attested receipt to the exact validated answer/citations/metadata."""
    return _signature(observation.model_dump(mode="json"))


def build_ai_visibility_plan(spec: ExperimentSpec) -> ExperimentPlan:
    """Build a reproducible, non-executing plan or fail before any provider work."""
    if not isinstance(spec, ExperimentSpec):
        spec = ExperimentSpec.model_validate(spec)
    expected = (len(spec.markets) * len(spec.questions) *
                len(spec.providers) * spec.repetitions)
    if expected > spec.max_planned_runs:
        raise QuotaExceeded(
            f"{expected} planned AI requests exceed max_planned_runs "
            f"{spec.max_planned_runs}; nothing executed or truncated."
        )
    max_cost = sum(
        (Decimal(len(spec.markets) * len(spec.questions) * spec.repetitions) *
         provider.max_charge_per_run_usd for provider in spec.providers),
        Decimal("0"),
    )
    if max_cost > spec.max_estimated_total_usd:
        raise BudgetExceeded(
            "Estimated owner-provided maximum USD " + str(max_cost) +
            " exceeds the budget USD " + str(spec.max_estimated_total_usd) +
            "; no work executed."
        )
    requests: list[PlannedAIRequest] = []
    no_location_parameter = 0
    maximum_search_uses = 0
    for provider in spec.providers:
        for market in spec.markets:
            for question in spec.questions:
                for repetition in range(1, spec.repetitions + 1):
                    if provider.location_support == LocationSupport.APPROXIMATE_MARKET:
                        market_params = {
                            "type": "approximate", "city": market.city,
                            "region": market.region, "country": market.country_code,
                        }
                        if market.timezone:
                            market_params["timezone"] = market.timezone
                        prompt = question.wording
                    elif provider.location_support == LocationSupport.PROMPT_CONTEXT_ONLY:
                        market_params = None
                        prompt = (
                            question.wording +
                            "\nBusiness market context supplied in prompt: " +
                            market.city + ", " + market.region + ", " +
                            market.country_code
                        )
                    else:
                        market_params = None
                        prompt = question.wording
                    payload = {
                        "provider_id": provider.id,
                        "family": provider.family.value,
                        "model": provider.model,
                        "prompt": prompt,
                        "approximate_location": market_params,
                        "search_uses_ceiling": provider.max_web_search_uses,
                    }
                    request_hash = _signature(payload)
                    logical = {
                        "experiment_id": spec.experiment_id,
                        "brand_name": spec.brand_name,
                        "brand_domain": spec.brand_domain,
                        "provider_id": provider.id, "market_id": market.id,
                        "question_id": question.id, "repetition": repetition,
                        "request_signature": request_hash,
                    }
                    idempotency_key = _signature(logical)
                    localized = market_params is not None
                    no_location_parameter += int(not localized)
                    maximum_search_uses += provider.max_web_search_uses
                    requests.append(PlannedAIRequest(
                        id=idempotency_key[:20],
                        experiment_id=spec.experiment_id,
                        provider_id=provider.id,
                        provider_family=provider.family,
                        model=provider.model,
                        question_id=question.id,
                        market_id=market.id,
                        repetition=repetition,
                        original_question=question.wording,
                        request_json=canonical_json(payload),
                        request_signature=request_hash,
                        idempotency_key=idempotency_key,
                        location_sent_as_parameter=localized,
                        search_uses_ceiling=provider.max_web_search_uses,
                        estimated_cost_ceiling_usd=provider.max_charge_per_run_usd,
                    ))
    if len(requests) != expected or len({r.id for r in requests}) != expected:
        raise ValueError("Duplicate or missing experiment run IDs")
    return ExperimentPlan(
        experiment_id=spec.experiment_id,
        experiment_signature=_signature(spec.model_dump(mode="json")),
        requests=tuple(requests),
        logical_runs=expected, max_possible_search_uses=maximum_search_uses,
        estimated_cost_ceiling_usd=max_cost,
        missing_location_parameter_runs=no_location_parameter,
    )


def citation_is_brand_domain(url: str, brand_domain: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host == brand_domain or host.endswith("." + brand_domain)


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def summarize_ai_visibility(
    spec: ExperimentSpec, plan: ExperimentPlan,
    observations: Iterable[ProviderObservation], *,
    trusted_provider_receipts: Iterable[TrustedReceipt] = (),
) -> VisibilityReport:
    """Count only complete, verified source responses in rates.

    Trusted proofs must originate from separately authenticated server-side
    provider transport, not customer/client-supplied JSON.
    """
    if not isinstance(spec, ExperimentSpec):
        spec = ExperimentSpec.model_validate(spec)
    if plan != build_ai_visibility_plan(spec):
        raise ValueError("Research plan differs from the declared experiment")
    runs = {run.id: run for run in plan.requests}
    profiles = {provider.id: provider for provider in spec.providers}
    verified_keys: dict[tuple[str, str, str, str], TrustedReceipt] = {}
    for proof in trusted_provider_receipts:
        if not isinstance(proof, TrustedReceipt):
            proof = TrustedReceipt.model_validate(proof)
        request = runs.get(proof.request_id)
        if request is None:
            raise UntrustedReceipt("Trusted receipt references unknown run")
        if (request.request_signature != proof.request_signature
                or request.provider_id != proof.provider_id):
            raise UntrustedReceipt("Trusted receipt does not match planned provider")
        key = (proof.request_id, proof.request_signature,
               proof.provider_id, proof.receipt_id)
        if key in verified_keys:
            raise UntrustedReceipt("Duplicate trusted provider receipt")
        verified_keys[key] = proof

    seen = set()
    other_counts = {
        EvidenceOrigin.OWNER_IMPORT: 0,
        EvidenceOrigin.HISTORICAL: 0,
        EvidenceOrigin.RECONSTRUCTED: 0,
        EvidenceOrigin.SYNTHETIC: 0,
    }
    completed = 0
    known_brand = 0
    unknown_brand = 0
    mentions = 0
    citation_eligible = 0
    brand_cited = 0
    unverified = 0
    incomplete = 0
    for item in observations:
        if not isinstance(item, ProviderObservation):
            item = ProviderObservation.model_validate(item)
        if item.request_id in seen:
            raise ValueError("Duplicate run observation cannot be counted twice")
        seen.add(item.request_id)
        run = runs.get(item.request_id)
        if run is None:
            raise ValueError("Observation belongs to an unknown experiment run")
        if (run.request_signature != item.request_signature
                or run.provider_id != item.provider_id
                or run.model != item.model):
            raise ValueError("Observation mismatches the planned provider/model/query")
        if item.origin in other_counts:
            other_counts[item.origin] += 1
            continue
        key = (item.request_id, item.request_signature,
               item.provider_id, item.receipt_id)
        proof = verified_keys.get(key)
        if proof is None:
            unverified += 1
            continue
        if proof.observation_sha256 != observation_digest(item):
            raise UntrustedReceipt("Authenticated receipt does not match the observed response content")
        if item.state != AnswerState.COMPLETED:
            incomplete += 1
            continue
        completed += 1
        if item.brand_mentioned is None:
            unknown_brand += 1
        else:
            known_brand += 1
            mentions += int(item.brand_mentioned)
        if profiles[item.provider_id].citation_signal:
            citation_eligible += 1
            if any(citation_is_brand_domain(c, spec.brand_domain) for c in item.citations):
                brand_cited += 1

    return VisibilityReport(
        experiment_id=spec.experiment_id, planned_runs=plan.logical_runs,
        verified_completed_runs=completed, eligible_mention_runs=known_brand,
        eligible_citation_runs=citation_eligible,
        brand_mentions=mentions, brand_domain_citation_runs=brand_cited,
        mention_rate=_rate(mentions, known_brand),
        brand_domain_citation_rate=_rate(brand_cited, citation_eligible),
        unknown_brand_signal_runs=unknown_brand,
        owner_imported_records=other_counts[EvidenceOrigin.OWNER_IMPORT],
        historical_records=other_counts[EvidenceOrigin.HISTORICAL],
        reconstructed_records=other_counts[EvidenceOrigin.RECONSTRUCTED],
        synthetic_records=other_counts[EvidenceOrigin.SYNTHETIC],
        unverified_receipt_claims=unverified,
        incomplete_or_failed_records=incomplete,
        not_observed_or_missing_runs=plan.logical_runs - completed,
    )
