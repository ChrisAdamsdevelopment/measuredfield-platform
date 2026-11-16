"""MF-017: deterministic module framework tests, zero external calls."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.module_catalog import MODULES, catalog, connection_policy, get_module
from app.services.visibility_lab import (
    BENCHMARK_22, LabInputError, import_gsc_csv, plan_experiment,
    propose_questions, provenance_counts, transform_question,
)


def benchmark():
    return [{"id": f"q-seed-{i}", "text": q} for i, q in enumerate(BENCHMARK_22, 1)]


def test_module_catalog_is_pure_and_never_claims_remote_access():
    items = catalog()
    assert len(items) >= 5
    assert len({m["id"] for m in items}) == len(items)
    assert get_module("visibility").path == "/app/modules/visibility"
    assert get_module("computer-connect").desktop_agent_enabled is False
    assert all(not m["live_provider_enabled"] for m in items)
    assert connection_policy()["default_access"] == "deny"
    assert connection_policy()["enabled"] is False


def test_original_22_question_benchmark_kept_unchanged():
    assert len(BENCHMARK_22) == 22
    assert BENCHMARK_22[0].startswith("Best place to get custom crown molding")
    assert BENCHMARK_22[4].startswith("Can I hire Prescotts Best Construction")
    assert BENCHMARK_22[-1].startswith("How do I contact Prescotts Best Construction")
    assert len({q.casefold() for q in BENCHMARK_22}) == 22


def test_all_22_are_planned_instead_of_silently_truncated_to_ten():
    plan = plan_experiment(benchmark(), ["Prescott Valley, Arizona"], ["as_written"])
    assert plan["request_count"] == 22
    assert plan["chunk_count"] == 5
    assert plan["provider_requests_executed"] == 0


def test_all_three_methods_plan_66_calls_without_implicit_cap():
    plan = plan_experiment(benchmark(), ["Prescott Valley, Arizona"],
                           ["as_written", "controlled_substitution", "persona_context"],
                           chunk_size=6, max_total=100)
    assert plan["request_count"] == 66
    assert plan["chunk_count"] == 11


def test_27_approved_questions_can_be_planned_with_no_hidden_limit():
    questions = benchmark() + [{"id": f"gsc-{i}", "text": f"Who handles remodel job type {i} in Prescott?"}
                                for i in range(5)]
    plan = plan_experiment(questions, ["Prescott, Arizona"], ["as_written"], max_total=50)
    assert plan["request_count"] == 27


def test_two_market_comparison_requires_exact_44_requests():
    plan = plan_experiment(benchmark(), ["Prescott, Arizona", "Prescott Valley, Arizona"],
                           ["controlled_substitution"], max_total=44)
    assert plan["request_count"] == 44
    assert plan["chunk_count"] == 9


def test_budget_failure_is_explicit_no_truncation():
    with pytest.raises(LabInputError, match="Requested 66 calls"):
        plan_experiment(benchmark(), ["Prescott Valley, Arizona"],
                        ["as_written", "controlled_substitution", "persona_context"], max_total=10)


def test_branded_company_name_immune_to_market_mutation():
    q = "How do I contact Prescotts Best Construction for home remodeling in Prescott?"
    result = transform_question(q, "Dewey-Humboldt, Arizona", "controlled_substitution")
    assert "Prescotts Best Construction" in result["actual_query_sent"]
    assert "Dewey-Humboldt" in result["actual_query_sent"]
    assert "Dewey-Humboldts Best Construction" not in result["actual_query_sent"]


def test_as_written_preserves_raw_question():
    q = "Best remodeling services in the greater Prescott area?"
    assert transform_question(q, "Chino Valley, Arizona", "as_written")["actual_query_sent"] == q


def test_csv_import_header_quoted_names_and_real_position():
    csv_text = ('Query,Clicks,Impressions,CTR,Position\n'
                '"general contractor in prescott, az",0,10,0%,1.0\n'
                'prescott valley kitchen contractor,0,8,0%,28.8\n')
    parsed = import_gsc_csv(csv_text)
    assert parsed["row_count"] == 2
    assert parsed["impressions"] == 18
    assert parsed["rows"][0]["avg_position"] == 1.0
    assert parsed["rows"][1]["category"] == "Kitchen remodeling"
    assert parsed["date_range"] is None
    assert parsed["property_verified"] is False


def test_csv_import_four_column_no_header_and_tab_delimited():
    csv_text = '"general contractor in prescott, az",0,10,1.0\ncontractors prescott az,0,45,41.6'
    rows = import_gsc_csv(csv_text)["rows"]
    assert len(rows) == 2
    assert rows[1]["avg_position"] == 41.6
    tsv = 'Query\tClicks\tImpressions\tPosition\ncustom trim prescott\t0\t2\t7.0\n'
    assert import_gsc_csv(tsv)["rows"][0]["query"] == "custom trim prescott"


def test_duplicate_gsc_rows_and_wrong_click_count_flagged():
    data = 'Query,Clicks,Impressions,Position\na,0,1,1\na,0,1,1\nb,4,1,1'
    parsed = import_gsc_csv(data)
    assert parsed["row_count"] == 1
    assert parsed["invalid_count"] == 2
    assert all("row" in err for err in parsed["errors"])


def test_question_suggestions_are_reviewable_and_source_linked():
    csv_text = 'Query,Clicks,Impressions,Position\nprescott general contractors,0,25,7.1\n'
    csv_text += 'kitchen remodeling prescott valley az,0,8,28.8\n'
    csv_text += 'construction superintendent recruiters in prescott, az,0,1,46.0\n'
    data = import_gsc_csv(csv_text)
    suggestions = propose_questions(data["rows"])
    assert len(suggestions) == 2
    assert all(item["status"] == "draft_needs_approval" for item in suggestions)
    assert all(item["source_queries"] for item in suggestions)
    assert not any("superintendent" in item["question"] for item in suggestions)


def test_provenance_quarantines_synthetic_and_reconstructed():
    runs = [
        {"id": "run-seed-1", "executionMode": "imported_baseline"},
        {"id": "run-live-preserved-1", "executionMode": "quarantined_fixture"},
        {"id": "run-fresh78-7", "executionMode": "live_grounded", "groundingStatus": "grounded_verified"},
        {"id": "run-live-123", "executionMode": "live_grounded", "groundingStatus": "grounded_verified"},
        {"id": "run-verified", "provider_receipt_verified": True,
         "raw_provider_response": "{\"response\":\"real\"}", "groundingStatus": "grounded_verified"},
    ]
    counts = provenance_counts(runs)
    assert counts == {
        "historical": 1, "quarantined": 1, "synthetic": 1,
        "captured_unverified": 1, "verified_provider": 1,
    }


def test_templates_render_with_a_synthetic_authenticated_context():
    repo = Path(__file__).resolve().parents[1]
    env = Environment(loader=FileSystemLoader(str(repo / "app" / "templates")),
                      autoescape=select_autoescape(["html"]))
    user = SimpleNamespace(name="Demo Owner", email="demo@example.invalid", is_platform_admin=False)
    principal = SimpleNamespace(user=user)
    base = dict(org=SimpleNamespace(name="Example Company", status="active"),
                principal=principal, settings=SimpleNamespace(is_production=False),
                role="owner", orgs=[], brand="MeasuredField", path="/app/modules",
                flash="", flash_error="", csrf="synthetic-csrf", request=object())
    html = env.get_template("app/modules.html").render(**base, modules=catalog(),
                                                          connection=connection_policy())
    assert "AI Visibility Lab" in html
    assert "Computer Connect" in html
    assert 'Remote access enabled: <strong>No</strong>' in html
    lab = env.get_template("app/visibility_lab.html").render(
        **{**base, "path": "/app/modules/visibility"},
        questions=benchmark(), markets=["Prescott Valley, Arizona", "Prescott, Arizona"],
        modes=["as_written", "controlled_substitution", "persona_context"])
    assert 'id="ml-all"' in lab
    assert 'id="ml-gsc-results"' in lab
    assert lab.count('class="ml-benchmark"') == 22
    assert 'id="ml-plan"' in lab


def test_runtime_routes_require_workspace_membership_and_are_dev_only():
    from app.routers import module_lab
    paths = {route.path for route in module_lab.router.routes}
    assert "/app/modules" in paths
    assert "/app/modules/visibility" in paths
    assert "/api/v1/modules/visibility/gsc/analyze" in paths
    assert "/api/v1/modules/visibility/plan" in paths
    assert module_lab._require_dev_preview.__name__ == "_require_dev_preview"
