"""Pure owner-readable evidence summary tests. No provider or database required."""
from __future__ import annotations

import pytest

from app.services.visibility_owner_snapshot import summarize_groups


def test_no_verified_receipts_means_no_fake_zero_percent():
    result = summarize_groups([])
    assert result["record_count"] == 0
    assert result["verified_answered"] == 0
    assert result["verified_mention_percent"] is None
    assert result["status"] == "awaiting_provider_receipts"
    assert "not independently verified" in result["headline"]


def test_synthetic_and_imported_cohorts_never_boost_verified_rate():
    data = [
        ("historical_import", True, 6),
        ("historical_import", False, 16),
        ("synthetic_fixture", True, 78),
        ("captured_unverified", True, 32),
        ("captured_unverified", False, 36),
        ("quarantined_reconstruction", True, 22),
    ]
    result = summarize_groups(data)
    assert result["record_count"] == 190
    assert result["verified_receipts"] == 0
    assert result["verified_mentioned"] == 0
    assert result["verified_mention_percent"] is None
    assert result["excluded_or_unverified"] == 190
    assert "one-day improvement" in result["comparison_note"]


def test_only_known_verified_results_enter_verified_denominator():
    data = [
        ("verified_provider", True, 3),
        ("verified_provider", False, 2),
        ("verified_provider", None, 1),
        ("historical_import", True, 30),
        ("captured_unverified", True, 40),
    ]
    result = summarize_groups(data)
    assert result["verified_receipts"] == 6
    assert result["verified_answered"] == 5
    assert result["verified_unknown"] == 1
    assert result["verified_mentioned"] == 3
    assert result["verified_mention_percent"] == 60.0
    assert result["record_count"] == 76
    assert result["status"] == "verified_data"


def test_unknown_and_malformed_origin_never_elevated():
    result = summarize_groups([
        ("provider_verified_because_i_say_so", True, 25),
        ("", False, 5),
        ("VERIFIED_PROVIDER", True, 5),
        (None, True, 1),
    ])
    review = next(row for row in result["buckets"] if row["key"] == "needs_review")
    assert review["records"] == 36
    assert result["verified_receipts"] == 0
    assert result["verified_mention_percent"] is None


@pytest.mark.parametrize("amount", [-2, -1, None, 2.0, True])
def test_invalid_group_counts_rejected(amount):
    with pytest.raises(ValueError, match="count"):
        summarize_groups([("verified_provider", True, amount)])


def test_boolean_only_results_no_int_or_string_coercion():
    result = summarize_groups([
        ("verified_provider", 1, 8),  # not bool
        ("verified_provider", "true", 5),  # not bool
        ("verified_provider", None, 1),
    ])
    assert result["verified_receipts"] == 14
    assert result["verified_answered"] == 0
    assert result["verified_mention_percent"] is None


def test_summary_has_no_revenue_or_google_ranking_claims():
    result = summarize_groups([("captured_unverified", True, 2)])
    assert "not Google rankings" in result["method_note"]
    assert "telephone calls" in result["method_note"]
    assert "chart screenshots" in result["import_note"]
