"""Plain-English, fail-closed evidence health counts for the private AI Visibility Lab.

Never merge benchmark/imported/synthetic cohorts into a claimed live improvement.
No provider calls, billing, evidence mutation, or PBC-specific hardcoded stats.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy import and_, case, func, select

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


def summarize_groups(groups: Iterable[tuple[str, bool | None, int]]) -> dict:
    """Summarize *already tenant-scoped* grouped rows without trusting client labels."""
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
    return {
        "headline": (
            "Verified AI results are available"
            if with_outcome else "AI recommendation results are not independently verified yet"
        ),
        "verified_receipts": verified,
        "verified_answered": with_outcome,
        "verified_mentioned": validated_mentions,
        # Important: None, never a fabricated 0% rate when no eligible results.
        "verified_mention_percent": (
            round(100 * validated_mentions / with_outcome, 1) if with_outcome else None
        ),
        "verified_unknown": verified - with_outcome,
        "record_count": sum(counts.values()),
        "excluded_or_unverified": sum(counts.values()) - verified,
        "buckets": details,
        "status": "verified_data" if with_outcome else "awaiting_provider_receipts",
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
    eligible = and_(
        record.provenance == "verified_provider",
        record.source_metadata["verified_by_server"].astext == "true",
        func.length(record.source_metadata["provider_receipt_id"].astext) >= 8,
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
    return summarize_groups(
        (kind, mentioned, int(amount)) for kind, mentioned, amount in rows
    )
