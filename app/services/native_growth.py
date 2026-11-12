"""Deterministic Python fallback for growth review when Node.js is unavailable.

This is an explicitly separate rule set, NOT a claim of byte-for-byte parity with
the reused PBC/Forge Node evaluator. No invented traffic, revenue or search ranking.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _counter(measured: dict, key: str) -> int:
    value = measured.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, min(int(value), 1_000_000_000))


def review_from_counts(business_name: str, measured: dict, *, days: int = 30) -> dict:
    views = _counter(measured, "page_view")
    starts = _counter(measured, "form_start")
    inquiries = _counter(measured, "lead")
    observed = bool(views or starts or inquiries)
    actions: list[dict] = []

    def propose(claim: str, experiment: str, metric: str, priority: str, evidence: str):
        actions.append({
            "claim": claim, "experiment": experiment, "metric": metric,
            "priority": priority, "evidenceStatus": evidence,
            "approvalRequired": True, "status": "proposed",
        })

    if not observed:
        propose(
            "Establish a trustworthy 30-day measurement baseline.",
            "Verify the customer website is published and its first-party page and inquiry "
            "counters are recording real activity before diagnosing conversions.",
            "Valid page-view, form-start and inquiry totals in the next 30 days",
            "high", "unknown_no_observed_counters",
        )
    else:
        if views >= 30 and starts <= max(1, views // 50):
            propose(
                "Few form starts are recorded relative to observed page views.",
                "Preview a clearer, mobile-visible primary inquiry button; have the owner "
                "approve a single versioned change before comparing later counts.",
                "Form starts per observed page view (before versus after)",
                "high", "proposed_from_observed_counts",
            )
        if starts >= 8 and inquiries * 2 < starts:
            propose(
                "Fewer than half of recorded form starts became recorded inquiries.",
                "Review form friction and required fields; test a shorter owner-approved "
                "intake form without removing fraud or consent controls.",
                "Recorded form starts and successful inquiries, by version",
                "high", "proposed_from_observed_counts",
            )
        if inquiries > 0:
            propose(
                "Recorded inquiries can now support a measured follow-up process.",
                "Review the inquiry desk daily and track first response status timestamps. "
                "Do not infer actual calls or emails solely from workflow status.",
                "Median recorded first-response time and overdue open inquiries",
                "medium", "measured_first_party",
            )
        if not actions:
            propose(
                "Keep collecting data before drawing a conversion conclusion.",
                "Document a single owner-approved website or intake improvement, then "
                "compare matched observation periods with enough visits to interpret.",
                "Page views, form starts, inquiries and observation sample sizes",
                "medium", "insufficient_sample_for_diagnostic",
            )

    caveats = [
        "Search impressions, rankings and click-through are unknown without authorized Search Console data.",
        "Visibility in AI assistant answers is unknown without a separate recorded evaluation.",
        "Revenue, job conversion and advertising attribution are unknown without connected records.",
    ]
    if starts > views or inquiries > starts:
        caveats.append(
            "The aggregate event counters are not internally consistent; investigate tracking before comparing conversion rates."
        )
    return {
        "businessName": business_name[:120],
        "days": days,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "engine": "measuredfield_native_fallback_v1",
        "evidenceStatus": {"site": "measured_first_party" if observed else "unknown",
                           "searchConsole": "unavailable", "ai": "unavailable"},
        "analysis": {
            "actions": actions,
            "methodology": "Transparent deterministic Python fallback. Only the platform's "
                           "recorded first-party counters are used. Suggestions are proposals "
                           "that require owner approval, not confirmed causes or automatic changes.",
        },
        "unknowns": caveats,
    }
