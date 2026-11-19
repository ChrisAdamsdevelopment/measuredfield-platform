"""Synthetic AI Visibility provider adapter tests, all network-free."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.ai_visibility_run import (
    AnswerState, EvidenceOrigin, ExperimentSpec, LocationSupport, Market,
    ProviderFamily, ProviderObservation, ProviderProfile, Question, TrustedReceipt,
)
from app.services.ai_visibility_adapter import (
    BudgetExceeded, QuotaExceeded, UntrustedReceipt,
    build_ai_visibility_plan, citation_is_brand_domain, observation_digest,
    summarize_ai_visibility,
)


@pytest.fixture
def specification():
    return ExperimentSpec(
        experiment_id="mf-demo-experiment-1",
        brand_name="Canyon Kitchen Company",
        brand_domain="https://www.canyon.example/",
        markets=(
            Market(id="prescott", city="Prescott", region="Arizona",
                   country_code="US", timezone="America/Phoenix"),
            Market(id="phoenix", city="Phoenix", region="Arizona",
                   country_code="US", timezone="America/Phoenix"),
        ),
        questions=(
            Question(id="q1", wording="Who remodels kitchens near Prescott?"),
            Question(id="q2", wording="Which contractor builds accessible bathrooms?"),
        ),
        providers=(
            ProviderProfile(
                id="openai-lab", family=ProviderFamily.OPENAI_WEB_SEARCH,
                model="openai-test-model", location_support=LocationSupport.APPROXIMATE_MARKET,
                max_charge_per_run_usd=Decimal("0.20"), max_web_search_uses=2,
            ),
            ProviderProfile(
                id="gemini-lab", family=ProviderFamily.GEMINI_GOOGLE_SEARCH,
                model="gemini-test-model", location_support=LocationSupport.PROMPT_CONTEXT_ONLY,
                max_charge_per_run_usd=Decimal("0.15"), max_web_search_uses=3,
            ),
        ),
        repetitions=2,
        max_planned_runs=20,
        max_estimated_total_usd=Decimal("5.00"),
    )


def test_source_plan_is_not_executed_and_has_precise_counts(specification):
    plan = build_ai_visibility_plan(specification)
    assert plan.logical_runs == 16
    assert len(plan.requests) == 16
    assert plan.max_possible_search_uses == 40
    assert plan.estimated_cost_ceiling_usd == Decimal("2.80")
    assert plan.missing_location_parameter_runs == 8
    assert not plan.live_execution_performed
    assert {r.status for r in plan.requests} == {"planned_not_executed"}
    assert len({r.id for r in plan.requests}) == 16


def test_reproducible_run_keys_and_repetitions(specification):
    first = build_ai_visibility_plan(specification)
    second = build_ai_visibility_plan(specification)
    assert first == second
    same = [r for r in first.requests
            if r.provider_id == "openai-lab" and r.market_id == "prescott" and
            r.question_id == "q1"]
    assert len(same) == 2
    assert same[0].repetition != same[1].repetition
    assert same[0].idempotency_key != same[1].idempotency_key
    assert same[0].request_signature == same[1].request_signature


def test_openai_uses_approximate_market_not_gps(specification):
    runs = build_ai_visibility_plan(specification).requests
    for run in (r for r in runs if r.provider_family == ProviderFamily.OPENAI_WEB_SEARCH):
        payload = json.loads(run.request_json)
        assert payload["approximate_location"]["type"] == "approximate"
        assert payload["approximate_location"]["city"] in ("Phoenix", "Prescott")
        assert payload["approximate_location"]["country"] == "US"
        assert payload["approximate_location"]["timezone"] == "America/Phoenix"
        assert "lat" not in run.request_json and "longitude" not in run.request_json
        assert run.location_sent_as_parameter


def test_gemini_context_not_presented_as_api_geo_targeting(specification):
    runs = build_ai_visibility_plan(specification).requests
    for run in (r for r in runs if r.provider_family == ProviderFamily.GEMINI_GOOGLE_SEARCH):
        payload = json.loads(run.request_json)
        assert payload["approximate_location"] is None
        assert "Business market context supplied in prompt" in payload["prompt"]
        assert run.location_sent_as_parameter is False


def test_none_location_reuses_exact_request_without_faking_separate_city_results(specification):
    providers = [
        ProviderProfile(id="no-location", family=ProviderFamily.OPENAI_WEB_SEARCH,
                        model="no-location", location_support=LocationSupport.NONE,
                        max_charge_per_run_usd=Decimal("0.10"),
                        max_web_search_uses=1)
    ]
    spec = specification.model_copy(update={"providers": tuple(providers), "repetitions": 1})
    plan = build_ai_visibility_plan(spec)
    a, b = [r for r in plan.requests if r.question_id == "q1"]
    assert a.market_id != b.market_id
    assert a.request_signature == b.request_signature
    assert not a.location_sent_as_parameter
    assert not b.location_sent_as_parameter


def test_quota_rejects_untruncated_experiment(specification):
    spec = specification.model_copy(update={"max_planned_runs": 15})
    with pytest.raises(QuotaExceeded, match="16 planned"):
        build_ai_visibility_plan(spec)


def test_spending_ceiling_rejects_preflight(specification):
    spec = specification.model_copy(update={"max_estimated_total_usd": Decimal("1.00")})
    with pytest.raises(BudgetExceeded, match="exceeds"):
        build_ai_visibility_plan(spec)


def test_missing_explicit_cost_ceiling_is_validation_error():
    with pytest.raises(ValidationError):
        ProviderProfile(id="unknown", family=ProviderFamily.OPENAI_WEB_SEARCH,
                        model="unknown-model",
                        location_support=LocationSupport.APPROXIMATE_MARKET,
                        max_web_search_uses=1)


def test_live_execution_switch_is_never_allowed(specification):
    with pytest.raises(ValidationError, match="cannot execute providers"):
        ExperimentSpec.model_validate({
            **specification.model_dump(), "live_execution_enabled": True
        })


@pytest.mark.parametrize("family,location", [
    (ProviderFamily.OPENAI_WEB_SEARCH, LocationSupport.PROMPT_CONTEXT_ONLY),
    (ProviderFamily.ANTHROPIC_WEB_SEARCH, LocationSupport.PROMPT_CONTEXT_ONLY),
    (ProviderFamily.GEMINI_GOOGLE_SEARCH, LocationSupport.APPROXIMATE_MARKET),
    (ProviderFamily.CUSTOM_ATTESTED, LocationSupport.APPROXIMATE_MARKET),
])
def test_provider_cannot_claim_unsupported_location(family, location):
    with pytest.raises(ValidationError):
        ProviderProfile(id="provider", family=family, model="model",
                        location_support=location, max_charge_per_run_usd="0.1",
                        max_web_search_uses=2)


def test_anthropic_approximate_market_is_explicit_and_supported():
    provider = ProviderProfile(
        id="anthropic", family=ProviderFamily.ANTHROPIC_WEB_SEARCH,
        model="claude-test", location_support=LocationSupport.APPROXIMATE_MARKET,
        max_charge_per_run_usd=Decimal("0.15"), max_web_search_uses=3)
    assert provider.location_support == LocationSupport.APPROXIMATE_MARKET


def test_bad_duplicate_question_ids_and_invalid_brand_domain(specification):
    with pytest.raises(ValidationError, match="Duplicate question IDs"):
        ExperimentSpec.model_validate({
            **specification.model_dump(),
            "questions": [specification.questions[0].model_dump()] * 2,
        })
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({
            **specification.model_dump(),
            "brand_domain": "https://canyon.example/about",
        })


@pytest.mark.parametrize("wording", [
    "Line 1\nLine 2", "A\tTab contained", " ",
])
def test_question_wording_validation(wording):
    with pytest.raises(ValidationError):
        Question(id="q1", wording=wording)


def _observation(run, **changes):
    data = dict(
        request_id=run.id, request_signature=run.request_signature,
        provider_id=run.provider_id, model=run.model,
        observed_at=datetime(2026, 11, 16, 15, tzinfo=timezone.utc),
        receipt_id="verified-receipt-" + run.id,
        origin=EvidenceOrigin.VERIFIED_PROVIDER,
        state=AnswerState.COMPLETED,
        brand_mentioned=True,
        citations=("https://www.canyon.example/service/page?ref=ai",),
    )
    data.update(changes)
    return ProviderObservation(**data)


def _proof(obs):
    return TrustedReceipt(
        request_id=obs.request_id, request_signature=obs.request_signature,
        provider_id=obs.provider_id, receipt_id=obs.receipt_id,
        verification_method="provider_api_response",
        observation_sha256=observation_digest(obs),
        verified_at=datetime(2026, 11, 16, 16, tzinfo=timezone.utc),
    )


def test_verified_response_summary_counts_mentions_and_brand_links(specification):
    plan = build_ai_visibility_plan(specification)
    first, second = plan.requests[0], plan.requests[1]
    a = _observation(first)
    b = _observation(second, brand_mentioned=False,
                     citations=("https://another.example/thing",))
    report = summarize_ai_visibility(
        specification, plan, (a, b), trusted_provider_receipts=(_proof(a), _proof(b))
    )
    assert report.planned_runs == 16
    assert report.verified_completed_runs == 2
    assert report.eligible_mention_runs == 2
    assert report.eligible_citation_runs == 2
    assert report.brand_mentions == 1
    assert report.brand_domain_citation_runs == 1
    assert report.mention_rate == Decimal("0.5000")
    assert report.brand_domain_citation_rate == Decimal("0.5000")
    assert report.not_observed_or_missing_runs == 14


def test_unverified_claims_cannot_mint_verified_denominator(specification):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(plan.requests[0])
    report = summarize_ai_visibility(specification, plan, (obs,))
    assert report.verified_completed_runs == 0
    assert report.unverified_receipt_claims == 1
    assert report.mention_rate is None
    assert report.brand_domain_citation_rate is None


def test_synthetic_import_reconstruction_history_remain_out_of_verified_rates(specification):
    plan = build_ai_visibility_plan(specification)
    sources = [
        EvidenceOrigin.SYNTHETIC, EvidenceOrigin.OWNER_IMPORT,
        EvidenceOrigin.HISTORICAL, EvidenceOrigin.RECONSTRUCTED,
    ]
    observations = [_observation(run, origin=kind) for run, kind
                    in zip(plan.requests, sources)]
    report = summarize_ai_visibility(specification, plan, observations)
    assert (report.synthetic_records, report.owner_imported_records,
            report.historical_records, report.reconstructed_records) == (1, 1, 1, 1)
    assert report.eligible_mention_runs == 0
    assert report.mention_rate is None
    assert report.brand_domain_citation_rate is None


def test_known_brand_unknown_signal_is_excluded_not_counted_as_absence(specification):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(plan.requests[0], brand_mentioned=None)
    report = summarize_ai_visibility(
        specification, plan, (obs,), trusted_provider_receipts=(_proof(obs),)
    )
    assert report.verified_completed_runs == 1
    assert report.eligible_mention_runs == 0
    assert report.unknown_brand_signal_runs == 1
    assert report.mention_rate is None
    assert report.brand_domain_citation_rate == Decimal("1.0000")


def test_failed_verified_response_is_not_denominator(specification):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(
        plan.requests[0], state=AnswerState.FAILED,
        brand_mentioned=None, citations=(),
    )
    report = summarize_ai_visibility(
        specification, plan, (obs,), trusted_provider_receipts=(_proof(obs),)
    )
    assert report.verified_completed_runs == 0
    assert report.incomplete_or_failed_records == 1
    assert report.mention_rate is None


@pytest.mark.parametrize("invalid", [
    {"state": AnswerState.FAILED, "brand_mentioned": True},
    {"state": AnswerState.UNKNOWN, "citations": ("https://example.com/",)},
    {"observed_at": datetime(2026, 11, 16, 12)},
    {"citations": ("javascript:alert(1)",)},
    {"citations": ("https://username:password@canyon.example/",)},
    {"citations": ("ftp://canyon.example",)},
])
def test_invalid_unverified_response_state_and_citations(specification, invalid):
    plan = build_ai_visibility_plan(specification)
    with pytest.raises(ValidationError):
        _observation(plan.requests[0], **invalid)


@pytest.mark.parametrize("tamper", [
    {"request_signature": "f" * 64},
    {"provider_id": "other-provider"},
    {"model": "swapped-model"},
])
def test_observation_cannot_rename_provider_or_query(specification, tamper):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(plan.requests[0], **tamper)
    with pytest.raises(ValueError, match="mismatches"):
        summarize_ai_visibility(specification, plan, (obs,))


def test_duplicate_provider_receipt_is_rejected(specification):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(plan.requests[0])
    proof = _proof(obs)
    with pytest.raises(UntrustedReceipt, match="Duplicate"):
        summarize_ai_visibility(
            specification, plan, (obs,),
            trusted_provider_receipts=(proof, proof),
        )


def test_duplicate_observation_cannot_count_twice(specification):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(plan.requests[0])
    with pytest.raises(ValueError, match="Duplicate run"):
        summarize_ai_visibility(specification, plan, (obs, obs),
                                trusted_provider_receipts=(_proof(obs),))


def test_foreign_or_replayed_proof_rejected(specification):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(plan.requests[0])
    foreign = _proof(obs).model_copy(update={"request_signature": "a" * 64})
    with pytest.raises(UntrustedReceipt):
        summarize_ai_visibility(
            specification, plan, (obs,),
            trusted_provider_receipts=(foreign,),
        )


def test_receipt_id_must_match_attestation_exactly(specification):
    plan = build_ai_visibility_plan(specification)
    obs = _observation(plan.requests[0])
    altered = _proof(obs).model_copy(update={"receipt_id": "someone-elses-response"})
    report = summarize_ai_visibility(
        specification, plan, (obs,),
        trusted_provider_receipts=(altered,),
    )
    assert report.verified_completed_runs == 0
    assert report.unverified_receipt_claims == 1


def test_citation_domain_not_confused_with_malicious_suffix():
    assert citation_is_brand_domain("https://sub.canyon.example/post", "canyon.example")
    assert not citation_is_brand_domain("https://canyon.example.evil.test/", "canyon.example")
    assert not citation_is_brand_domain("https://other.example/", "canyon.example")


def test_planned_run_is_not_evidence(specification):
    plan = build_ai_visibility_plan(specification)
    assert plan.live_execution_performed is False
    report = summarize_ai_visibility(specification, plan, ())
    assert report.not_observed_or_missing_runs == plan.logical_runs
    assert report.mention_rate is None


def test_report_rejects_unrelated_plan_even_with_same_provider(specification):
    plan = build_ai_visibility_plan(specification)
    other = specification.model_copy(update={"brand_name": "Different"})
    # Brand name changes report interpretation even without changing query parameters.
    with pytest.raises(ValueError, match="Research plan differs|specification"):
        summarize_ai_visibility(other, plan, ())


def test_no_sdk_network_api_keys_or_actual_charges():
    import app.services.ai_visibility_adapter as adapter
    import app.schemas.ai_visibility_run as schemas
    code = inspect.getsource(adapter)
    assert "requests.post(" not in code
    assert "httpx.post(" not in code
    assert "urlopen(" not in code
    assert "openai.responses" not in code
    assert "anthropic.Anthropic(" not in code
    assert "google.genai.Client(" not in code
    assert "os.environ" not in code


@pytest.mark.parametrize("tampered_fields", [
    {"brand_mentioned": False},
    {"citations": ("https://competitor.example/another",)},
    {"receipt_id": "same-request-different-response"},
    {"observed_at": datetime(2026, 11, 16, 17, tzinfo=timezone.utc)},
])
def test_verified_receipt_cannot_cover_changed_answer_or_citations(specification, tampered_fields):
    plan = build_ai_visibility_plan(specification)
    original = _observation(plan.requests[0])
    trusted = _proof(original)
    modified = original.model_copy(update=tampered_fields)
    if modified.receipt_id != original.receipt_id:
        report = summarize_ai_visibility(
            specification, plan, (modified,), trusted_provider_receipts=(trusted,))
        assert report.verified_completed_runs == 0
        assert report.unverified_receipt_claims == 1
    else:
        with pytest.raises(UntrustedReceipt, match="response content"):
            summarize_ai_visibility(
                specification, plan, (modified,), trusted_provider_receipts=(trusted,))
