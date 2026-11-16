"""MF-035: offline OpenAI Responses web-search request and citation evidence boundary.

This module has NO network client, credentials, persistence or route. Its output is
an UNVERIFIED structural preview, never an authenticated TrustedReceipt. The next
story must add tenant-approved transport, metered charging and server-side proof.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from app.schemas.ai_visibility_run import (
    ExperimentPlan, ExperimentSpec, LocationSupport, PlannedAIRequest, ProviderFamily,
)
from app.services.ai_visibility_adapter import build_ai_visibility_plan, canonical_json


class ProviderPreflightError(ValueError):
    """The signed experiment does not match the intended OpenAI provider call."""


class ProviderEvidenceError(ValueError):
    """An untrusted provider-shaped response cannot establish search evidence."""


@dataclass(frozen=True, slots=True)
class OpenAIWebSearchRequest:
    """One exact owner-planned run, NOT a sent API request."""
    run_id: str
    experiment_signature: str
    provider_id: str
    request_signature: str
    idempotency_key: str
    endpoint: str
    body: dict[str, Any]
    requests_sent: int = 0
    estimated_cost_ceiling_usd: str = "0"
    status: str = "not_executed"


@dataclass(frozen=True, slots=True)
class OpenAIEvidencePreview:
    """Validated structure alone does not prove where a response originated."""
    run_id: str
    provider_id: str
    response_id: str
    response_model: str
    observed_at: datetime
    response_sha256: str
    web_search_calls: int
    cited_urls: tuple[str, ...]
    brand_mentioned_in_text: bool
    answer_excerpt: str
    origin: str = "unverified_provider_structure"
    trusted_provider_receipt_issued: bool = False
    eligible_for_verified_rates: bool = False


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strict_object(value: Any, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderEvidenceError(message)
    return value


def _request(spec: ExperimentSpec, plan: ExperimentPlan, run: PlannedAIRequest) -> tuple[dict, Any]:
    if plan != build_ai_visibility_plan(spec):
        raise ProviderPreflightError("Experiment no longer matches the saved plan")
    matches = [x for x in plan.requests if x.id == run.id]
    if len(matches) != 1 or matches[0] != run:
        raise ProviderPreflightError("The requested search is not an exact signed planned run")
    providers = [p for p in spec.providers if p.id == run.provider_id]
    if len(providers) != 1 or providers[0].family != ProviderFamily.OPENAI_WEB_SEARCH:
        raise ProviderPreflightError("This adapter supports only reviewed OpenAI web-search providers")
    provider = providers[0]
    if provider.model != run.model or provider.max_web_search_uses != run.search_uses_ceiling:
        raise ProviderPreflightError("Provider model or web-search limit changed after approval")
    try:
        canonical = json.loads(run.request_json)
    except (TypeError, ValueError):
        raise ProviderPreflightError("Planned request JSON is malformed") from None
    if not isinstance(canonical, dict) or canonical_json(canonical) != run.request_json:
        raise ProviderPreflightError("Planned parameters differ from the canonical signature")
    if _digest(run.request_json) != run.request_signature:
        raise ProviderPreflightError("Planned request parameters were modified")
    if canonical.get("family") != ProviderFamily.OPENAI_WEB_SEARCH.value:
        raise ProviderPreflightError("Unsupported provider family")
    if (canonical.get("provider_id") != run.provider_id or
            canonical.get("model") != run.model or
            canonical.get("search_uses_ceiling") != run.search_uses_ceiling):
        raise ProviderPreflightError("Provider metadata and planned signature disagree")
    if not isinstance(canonical.get("prompt"), str) or not canonical["prompt"].strip():
        raise ProviderPreflightError("A nonblank approved research question is required")
    allowed = {"provider_id", "family", "model", "prompt", "approximate_location", "search_uses_ceiling"}
    if set(canonical) != allowed:
        raise ProviderPreflightError("Unexpected parameters in the research plan")
    return canonical, provider


def build_openai_web_search_request(
    spec: ExperimentSpec, plan: ExperimentPlan, run: PlannedAIRequest
) -> OpenAIWebSearchRequest:
    """Prepare one explicit, fully-bounded Responses request. Does not send it."""
    canonical, provider = _request(spec, plan, run)
    location = canonical["approximate_location"]
    if provider.location_support == LocationSupport.APPROXIMATE_MARKET:
        if not isinstance(location, dict) or location.get("type") != "approximate":
            raise ProviderPreflightError("Declared approximate market was not supplied")
        if set(location) - {"type", "city", "region", "country", "timezone"}:
            raise ProviderPreflightError("Unexpected location parameters")
        if not all(isinstance(location.get(k), str) and location[k] for k in ("city", "region", "country")):
            raise ProviderPreflightError("Incomplete approximate market")
        if not re.fullmatch(r"[A-Z]{2}", location["country"]):
            raise ProviderPreflightError("Invalid country code")
        if "timezone" in location and (not isinstance(location["timezone"], str)
                                      or len(location["timezone"]) > 80):
            raise ProviderPreflightError("Invalid timezone parameter")
        if not run.location_sent_as_parameter:
            raise ProviderPreflightError("Location provenance disagrees with request")
    else:
        if location is not None or run.location_sent_as_parameter:
            raise ProviderPreflightError("Search plan claimed unsupported provider location")
    tool: dict[str, Any] = {"type": "web_search", "search_context_size": "medium"}
    if location is not None:
        tool["user_location"] = location
    body = {
        "model": provider.model,
        "input": canonical["prompt"],
        "tools": [tool],
        "tool_choice": "required",
        "max_tool_calls": run.search_uses_ceiling,
        "include": ["web_search_call.action.sources"],
        # No conversation memory or automatic background scheduling.
        "store": False,
    }
    return OpenAIWebSearchRequest(
        run_id=run.id, experiment_signature=plan.experiment_signature,
        provider_id=run.provider_id, request_signature=run.request_signature,
        idempotency_key=run.idempotency_key, endpoint="/v1/responses", body=body,
        estimated_cost_ceiling_usd=str(run.estimated_cost_ceiling_usd),
    )


def _safe_citation_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ProviderEvidenceError("Citation contains an invalid URL")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ProviderEvidenceError("Citation contains an invalid URL") from None
    if (parsed.scheme.lower() not in {"https", "http"} or
            not parsed.hostname or parsed.username or parsed.password or parsed.port is not None):
        raise ProviderEvidenceError("Citation does not identify a safe web source URL")
    return value


def _exact_brand_mention(answer: str, brand: str) -> bool:
    """Count literal whole brand phrase; do not conflate citations with mentions."""
    escaped = re.escape(brand.strip())
    return bool(re.search(r"(?<!\w)" + escaped + r"(?!\w)", answer, flags=re.IGNORECASE))


def inspect_openai_web_search_response(
    spec: ExperimentSpec, plan: ExperimentPlan, run: PlannedAIRequest, raw: dict[str, Any],
) -> OpenAIEvidencePreview:
    """Read an untrusted fixture/response-shaped object, issue NO provider attestation.

    An API caller, imported JSON or fake transport cannot create TrustedReceipt by
    passing a fabricated raw object here. A future server-only HTTP client must
    authenticate and validate the exchange before any verified report can be made.
    """
    built = build_openai_web_search_request(spec, plan, run)
    obj = _strict_object(raw, "OpenAI response must be a JSON object")
    try:
        raw_json = canonical_json(obj)
    except (TypeError, ValueError):
        raise ProviderEvidenceError("Response JSON was not bounded and serializable") from None
    if len(raw_json.encode("utf-8")) > 1_000_000:
        raise ProviderEvidenceError("Response exceeds bounded evidence size")
    response_id = obj.get("id")
    if not isinstance(response_id, str) or not re.fullmatch(r"resp_[A-Za-z0-9_-]{6,180}", response_id):
        raise ProviderEvidenceError("Missing provider-shaped response identifier")
    if obj.get("status") != "completed":
        raise ProviderEvidenceError("Incomplete or failed responses cannot establish observed results")
    if obj.get("model") != built.body["model"]:
        raise ProviderEvidenceError("Response model differs from planned provider")
    epoch = obj.get("created_at")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or not 1_500_000_000 <= epoch <= 4_000_000_000:
        raise ProviderEvidenceError("Response requires a plausible Unix timestamp")
    output = obj.get("output")
    if not isinstance(output, list) or not output or len(output) > 80:
        raise ProviderEvidenceError("Missing or excessive provider response output")
    seen_calls: set[str] = set()
    web_calls = 0
    actual_searches = 0
    answers: list[str] = []
    citations: list[str] = []
    for record in output:
        item = _strict_object(record, "Output entries must be JSON objects")
        kind = item.get("type")
        if kind == "web_search_call":
            if item.get("status") != "completed":
                raise ProviderEvidenceError("Web search call was not completed")
            call_id = item.get("id")
            if not isinstance(call_id, str) or not call_id or call_id in seen_calls:
                raise ProviderEvidenceError("Missing or duplicate web-search call ID")
            seen_calls.add(call_id)
            web_calls += 1
            action = item.get("action")
            if not isinstance(action, dict) or action.get("type") not in {"search", "open_page", "find_in_page"}:
                raise ProviderEvidenceError("Web search action was not verifiable")
            actual_searches += int(action.get("type") == "search")
        elif kind == "message":
            if item.get("role") != "assistant" or item.get("status") != "completed":
                raise ProviderEvidenceError("Only completed assistant output can be inspected")
            content = item.get("content")
            if not isinstance(content, list) or not content or len(content) > 40:
                raise ProviderEvidenceError("Assistant answer is incomplete")
            for segment in content:
                seg = _strict_object(segment, "Answer parts must be JSON objects")
                if seg.get("type") != "output_text" or not isinstance(seg.get("text"), str):
                    continue
                answers.append(seg["text"])
                annotation_list = seg.get("annotations", [])
                if not isinstance(annotation_list, list) or len(annotation_list) > 100:
                    raise ProviderEvidenceError("Citation annotations are malformed")
                for annotation in annotation_list:
                    ann = _strict_object(annotation, "Citation annotation must be a JSON object")
                    if ann.get("type") == "url_citation":
                        url = _safe_citation_url(ann.get("url"))
                        if url not in citations:
                            citations.append(url)
    if web_calls == 0 or actual_searches == 0:
        raise ProviderEvidenceError("No completed search action proves a searched response shape")
    if web_calls > run.search_uses_ceiling:
        raise ProviderEvidenceError("Response exceeded the planned search-call ceiling")
    if len(citations) > 100:
        raise ProviderEvidenceError("Response has too many citations")
    full_answer = "\n".join(answers)
    if not full_answer.strip():
        raise ProviderEvidenceError("No completed answer text was returned")
    return OpenAIEvidencePreview(
        run_id=run.id, provider_id=run.provider_id, response_id=response_id,
        response_model=obj["model"], observed_at=datetime.fromtimestamp(epoch, timezone.utc),
        response_sha256=_digest(raw_json), web_search_calls=web_calls,
        cited_urls=tuple(citations),
        brand_mentioned_in_text=_exact_brand_mention(full_answer, spec.brand_name),
        answer_excerpt=full_answer[:600],
    )
