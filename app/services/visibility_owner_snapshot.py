"""Plain-English, fail-closed evidence health counts for the private AI Visibility Lab.

Never merge benchmark/imported/synthetic cohorts into a claimed live improvement.
No provider calls, billing, evidence mutation, or PBC-specific hardcoded stats.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB

from ..models import VisibilityObservation

BUCKETS = (
    ("verified_provider", "Independently checked AI answers",
     "Server-verified provider receipts, recorded questions, and identifiable test settings."),
    ("captured_unverified", "Recorded answers that still need checking",
     "Imported responses with no independently authenticated provider receipt."),
    ("historical_import", "Older comparison records",
     "Historical reference results; their dates and test methods may differ."),
    ("synthetic_fixture", "Practice examples — excluded",
     "Generated test material used to check software, never genuine customer-search evidence."),
    ("quarantined_reconstruction", "Reconstructed records — excluded",
     "Rebuilt or incomplete examples that must not enter published results."),
    ("needs_review", "Unrecognized or incomplete records — excluded",
     "Missing receipt fields, unknown classifications, or records that require a security review."),
)
BUCKET_KEYS = frozenset(key for key, _, _ in BUCKETS)
RECEIPT_PATTERN = r"^[A-Za-z0-9_.:-]{8,200}$"
NO_PLAN_LABEL = "Checked answers not linked to a research plan"


def summarize_cohorts(rows: Iterable[tuple]) -> list[dict]:
    """One entry per research round (experiment). Rows are already tenant scoped and
    already restricted to server-verified records:
        (experiment_id, mentioned, amount, first_observed, last_observed, setting_count)

    Rounds are NEVER added together: different questions, places, models or dates make a
    combined percentage meaningless (for example 27.3% in one round and 47.1% in another
    is not a 35.9% result and not a measured improvement).
    """
    cohorts: dict = {}
    for experiment_id, mentioned, amount, first, last, settings in rows:
        if type(amount) is not int or amount < 0:
            raise ValueError("Evidence row count is invalid.")
        key = str(experiment_id) if experiment_id is not None else None
        c = cohorts.setdefault(key, {"experiment_id": key, "records": 0, "answered": 0, "mentioned": 0,
                                     "first_observed": None, "last_observed": None, "setting_count": 0})
        c["records"] += amount
        if type(mentioned) is bool:
            c["answered"] += amount
            c["mentioned"] += amount if mentioned else 0
        if first is not None and (c["first_observed"] is None or first < c["first_observed"]):
            c["first_observed"] = first
        if last is not None and (c["last_observed"] is None or last > c["last_observed"]):
            c["last_observed"] = last
        c["setting_count"] = max(c["setting_count"], int(settings or 0))
    out = []
    for c in cohorts.values():
        c["unknown"] = c["records"] - c["answered"]
        c["linked_to_plan"] = c["experiment_id"] is not None
        # Without a research plan there is no guarantee the answers share questions, places,
        # tools or dates, so no percentage is computed for them at all.
        c["mention_percent"] = (round(100 * c["mentioned"] / c["answered"], 1)
                                if c["answered"] and c["linked_to_plan"] else None)
        c["label"] = "Research round" if c["linked_to_plan"] else NO_PLAN_LABEL
        # More than one provider/model inside one round means the round itself mixes settings.
        c["mixed_settings"] = c["setting_count"] > 1
        for k in ("first_observed", "last_observed"):
            c[k] = c[k].isoformat() if hasattr(c[k], "isoformat") else c[k]
        out.append(c)
    out.sort(key=lambda c: (c["last_observed"] or "", c["experiment_id"] or ""))
    return out


def summarize_groups(groups: Iterable[tuple[str, bool | None, int]],
                     cohorts: list[dict] | None = None) -> dict:
    """Summarize *already tenant-scoped* grouped rows without trusting client labels.

    ``cohorts`` (from summarize_cohorts) decides whether ONE percentage may be shown: only
    when exactly one research round has yes/no results. A caller that passes no cohorts
    is treated as a single round (used by the bucket unit tests).
    """
    counts = defaultdict(int)
    yes = defaultdict(int)
    known = defaultdict(int)
    for provenance, mentioned, amount in groups:
        if type(amount) is not int or amount < 0:
            raise ValueError("Evidence row count is invalid.")
        bucket = provenance if provenance in BUCKET_KEYS else "needs_review"
        counts[bucket] += amount
        if type(mentioned) is bool:
            known[bucket] += amount
            if mentioned:
                yes[bucket] += amount

    verified = counts["verified_provider"]
    with_outcome = known["verified_provider"]
    validated_mentions = yes["verified_provider"]
    details = [
        {
            "key": key,
            "title": title,
            "explanation": explanation,
            "records": counts[key],
            "reported_mentions": yes[key] if key != "verified_provider" else validated_mentions,
            "reported_known": known[key],
            "verified": key == "verified_provider",
            "usable_in_live_rate": key == "verified_provider",
        }
        for key, title, explanation in BUCKETS
    ]
    if cohorts is None:
        cohorts = ([{"experiment_id": "single-round", "answered": with_outcome, "mentioned": validated_mentions}]
                   if verified else [])
    rounds_with_results = [c for c in cohorts if c.get("answered") and c.get("experiment_id") is not None]
    if cohorts is not None and not rounds_with_results and with_outcome:
        unplanned_only = True
    else:
        unplanned_only = False
    one = rounds_with_results[0] if len(rounds_with_results) == 1 else None
    if not verified:
        headline, status = "AI recommendation results are not independently verified yet", "awaiting_provider_receipts"
    elif not with_outcome:
        headline = "Checked AI answers are stored, but none has a clear yes or no result yet"
        status = "verified_without_outcomes"
    elif one is not None:
        headline, status = "Verified AI results are available", "verified_data"
    elif unplanned_only:
        headline = "Checked AI answers are stored, but they are not tied to a research plan"
        status = "verified_without_plan"
    else:
        headline = f"Verified AI results come from {len(rounds_with_results)} separate research rounds"
        status = "multiple_rounds"
    return {
        "headline": headline,
        "verified_receipts": verified,
        "verified_answered": with_outcome,
        "verified_mentioned": validated_mentions,
        # Important: None, never a fabricated 0% rate when no eligible results, and never one
        # number pooled across research rounds that cannot be compared.
        "verified_mention_percent": (
            round(100 * one["mentioned"] / one["answered"], 1) if one is not None else None
        ),
        "single_round_answered": one["answered"] if one is not None else 0,
        "single_round_mentioned": one["mentioned"] if one is not None else 0,
        "cohorts": cohorts,
        "verified_unknown": verified - with_outcome,
        "record_count": sum(counts.values()),
        "excluded_or_unverified": sum(counts.values()) - verified,
        "buckets": details,
        "status": status,
        "method_note": (
            "These figures count stored research questions, not Google rankings, "
            "homeowner visits, telephone calls, leads or completed jobs."
        ),
        "comparison_note": (
            "An earlier percentage and a later percentage are not a proven "
            "one-day improvement unless the same questions, locations, model "
            "and testing rules were repeated with authentic dated receipts."
        ),
        "import_note": (
            "Imported labels and chart screenshots cannot turn practice results "
            "into verified live provider observations."
        ),
    }


def _checked_source():
    """Permit the verified bucket ONLY for first-party server-attested records.

    UI fields and import APIs cannot set verified_by_server; imported source
    metadata is wrapped and forced False by visibility_store.import_evidence.
    A future trusted provider ingestion path must explicitly supply a receipt.
    """
    record = VisibilityObservation
    flag = record.source_metadata["verified_by_server"]
    receipt = record.source_metadata["provider_receipt_id"]
    eligible = and_(
        record.provenance == "verified_provider",
        # JSON types are checked, not just text: the STRING "true", or a receipt that is a
        # number, list, object or blank text, must never pass (->> text extraction would).
        func.jsonb_typeof(flag) == "boolean",
        flag == literal(True, type_=JSONB),   # binds the JSON boolean true, not the string "true"
        func.jsonb_typeof(receipt) == "string",
        receipt.astext.op("~")(RECEIPT_PATTERN),
        record.observed_at.is_not(None),
        func.length(record.provider) > 0,
        func.length(record.model_id) > 0,
        func.length(record.actual_prompt) > 0,
        func.length(record.answer) > 0,
    )
    return case(
        (eligible, "verified_provider"),
        (record.provenance == "verified_provider", "needs_review"),
        else_=record.provenance,
    )


def owner_evidence_health(db, org_id) -> dict:
    """Read only current owner's tenant records, with DB RLS as a second guard."""
    classification = _checked_source()
    rows = db.execute(
        select(classification.label("kind"), VisibilityObservation.reported_mentioned,
               func.count(VisibilityObservation.id))
        .where(VisibilityObservation.org_id == org_id)
        .group_by(classification, VisibilityObservation.reported_mentioned)
    ).all()
    record = VisibilityObservation
    cohort_rows = db.execute(
        select(record.experiment_id, record.reported_mentioned, func.count(record.id),
               func.min(record.observed_at), func.max(record.observed_at),
               func.count(func.distinct(func.concat(record.provider, "|", record.model_id))))
        .where(record.org_id == org_id, classification == "verified_provider")
        .group_by(record.experiment_id, record.reported_mentioned)
    ).all()
    cohorts = summarize_cohorts(
        (experiment_id, mentioned, int(amount), first, last, int(settings))
        for experiment_id, mentioned, amount, first, last, settings in cohort_rows
    )
    return summarize_groups(
        ((kind, mentioned, int(amount)) for kind, mentioned, amount in rows), cohorts=cohorts
    )
