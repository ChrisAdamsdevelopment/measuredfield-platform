"""MF-017: deterministic module framework tests, zero external calls."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.module_catalog import catalog, connection_policy, get_module
from app.services.visibility_lab import (
    BENCHMARK_22, BRAND_ALIASES, LabInputError, MARKETS,
    import_gsc_csv, plan_experiment, propose_questions,
    provenance_counts, tenant_lab_config, transform_question,
    validate_tenant_lab_settings,
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
    assert "/api/v1/modules/visibility/config" in paths
    assert "/api/v1/modules/visibility/gsc/analyze" in paths
    assert "/api/v1/modules/visibility/plan" in paths
    assert module_lab._require_dev_preview.__name__ == "_require_dev_preview"


def test_pilot_org_retains_seeded_benchmark_markets_and_aliases():
    from uuid import uuid4
    org = SimpleNamespace(id=uuid4(), name="Prescott's Best Construction", settings={})
    pilot_cfg = tenant_lab_config(org, pilot_org_id=str(org.id))
    assert pilot_cfg.is_pilot is True
    assert pilot_cfg.questions == BENCHMARK_22
    assert pilot_cfg.markets == MARKETS
    assert pilot_cfg.brand_aliases == BRAND_ALIASES


def test_second_business_in_another_city_plans_own_markets_and_classifies_own_brand():
    """Claude MF-017 review regression: a non-pilot business in another city uses its own
    service areas, brand aliases and benchmark questions without PBC leakage."""
    org = SimpleNamespace(
        name="Flagstaff's Best Remodeling LLC",
        settings={
            "visibility_lab": {
                "markets": ["Flagstaff, Arizona", "Sedona, Arizona", "Cottonwood, Arizona"],
            }
        },
    )
    cfg = tenant_lab_config(org)
    assert cfg.is_pilot is False
    assert cfg.markets == ("Flagstaff, Arizona", "Sedona, Arizona", "Cottonwood, Arizona")
    assert len(cfg.questions) == 22
    assert all("Prescott" not in q for q in cfg.questions)
    assert any("Flagstaff's Best Remodeling" in q for q in cfg.questions)
    assert any("Flagstaff" in q for q in cfg.questions)
    assert "Flagstaff's Best Remodeling" in cfg.brand_aliases
    assert "Flagstaffs Best Remodeling" in cfg.brand_aliases
    assert "Flagstaff Best Remodeling" in cfg.brand_aliases
    assert not any("Prescott" in alias for alias in cfg.brand_aliases)

    # GSC import classifies the second business's own brand queries as "brand"
    # and does not treat the pilot business's name as this tenant's brand.
    csv_text = (
        "Query,Clicks,Impressions,Position\n"
        "flagstaffs best remodeling reviews,4,30,1.4\n"
        "kitchen remodeling sedona az,1,22,6.5\n"
        "bathroom contractor flagstaff,0,15,9.0\n"
        "prescotts best construction,0,5,12.0\n"
    )
    parsed = import_gsc_csv(csv_text, config=cfg)
    by_query = {row["query"]: row for row in parsed["rows"]}
    assert by_query["flagstaffs best remodeling reviews"]["status"] == "brand"
    assert by_query["kitchen remodeling sedona az"]["status"] == "eligible"
    assert by_query["kitchen remodeling sedona az"]["geography"] == "Sedona, Arizona"
    assert by_query["bathroom contractor flagstaff"]["geography"] == "Flagstaff, Arizona"
    assert by_query["prescotts best construction"]["status"] == "eligible"

    proposals = propose_questions(parsed["rows"], config=cfg)
    assert any(p["geography"] == "Sedona, Arizona" and "Sedona, Arizona" in p["question"] for p in proposals)
    assert any(p["geography"] == "Flagstaff, Arizona" and "Flagstaff, Arizona" in p["question"] for p in proposals)
    assert all("Prescott" not in p["question"] for p in proposals)

    # Controlled substitution protects the tenant's brand even when the brand contains a city name.
    branded_q = "How do I contact Flagstaff's Best Remodeling for home remodeling in Flagstaff?"
    sub = transform_question(branded_q, "Sedona, Arizona", "controlled_substitution", config=cfg)
    assert "Flagstaff's Best Remodeling" in sub["actual_query_sent"]
    assert "in Sedona?" in sub["actual_query_sent"]
    assert "Sedona's Best Remodeling" not in sub["actual_query_sent"]

    # Planning succeeds for the tenant's own markets and rejects pilot markets outside its service areas.
    questions = [{"id": f"q-seed-{i}", "text": q} for i, q in enumerate(cfg.questions, 1)]
    plan = plan_experiment(
        questions,
        ["Flagstaff, Arizona", "Sedona, Arizona"],
        ["as_written", "controlled_substitution", "persona_context"],
        max_total=200,
        config=cfg,
    )
    assert plan["request_count"] == 132
    assert plan["market_count"] == 2
    with pytest.raises(LabInputError, match="Choose distinct supported markets"):
        plan_experiment(questions, ["Prescott Valley, Arizona"], ["as_written"], config=cfg)


def test_default_non_pilot_org_never_receives_pilot_questions_or_aliases():
    org = SimpleNamespace(name="Tucson Sun Kitchen & Bath Inc.", settings={})
    cfg = tenant_lab_config(org)
    assert cfg.is_pilot is False
    assert all("Prescott" not in q for q in cfg.questions)
    assert all("Prescott" not in m for m in cfg.markets)
    assert all("Prescott" not in a for a in cfg.brand_aliases)
    assert "Tucson Sun Kitchen & Bath" in cfg.brand_aliases
    assert "Tucson Sun Kitchen and Bath" in cfg.brand_aliases


def test_validate_tenant_lab_settings_rejects_unsafe_or_empty_inputs():
    valid = validate_tenant_lab_settings(
        business_name="Sedona Custom Homes LLC",
        markets=["Sedona, Arizona", "Cottonwood, Arizona"],
        brand_aliases=["Sedona Custom Homes", "SCH Remodeling"],
    )
    assert valid["markets"] == ["Sedona, Arizona", "Cottonwood, Arizona"]
    assert "SCH Remodeling" in valid["brand_aliases"]
    assert "Sedona Custom Homes" in valid["brand_aliases"]
    with pytest.raises(LabInputError):
        validate_tenant_lab_settings(business_name="Test", markets=[])
    with pytest.raises(LabInputError):
        validate_tenant_lab_settings(business_name="Test", markets=["<script>bad</script>"])


def test_authenticated_multi_tenant_visibility_lab_isolates_org_configs_and_markets(platform):
    """End-to-end PostgreSQL + FastAPI test with two synthetic businesses in different cities."""
    from .conftest import csrf_of, onboard_business
    from .test_pg_isolation import add_member

    flagstaff_owner, _, _ = onboard_business(
        platform,
        business="Flagstaff Peak Remodeling LLC",
        owner_email="flagstaff-lab-owner@example.invalid",
    )
    tucson_owner, _, _ = onboard_business(
        platform,
        business="Tucson Adobe Builders Inc",
        owner_email="tucson-lab-owner@example.invalid",
    )
    flagstaff_reader = add_member(
        platform, flagstaff_owner, "flagstaff-lab-reader@example.invalid", "readonly"
    )

    # Even before configuring custom markets, neither non-pilot business is offered PBC questions.
    initial_page = flagstaff_owner.get("/app/modules/visibility")
    assert initial_page.status_code == 200
    assert "Prescotts Best Construction" not in initial_page.text
    assert "Flagstaff Peak Remodeling" in initial_page.text

    # Readonly member cannot modify the tenant's visibility configuration.
    cfg_url = "/api/v1/modules/visibility/config"
    assert flagstaff_reader.post(
        cfg_url,
        json={"markets": ["Flagstaff, Arizona", "Sedona, Arizona"]},
        headers={"X-CSRF-Token": csrf_of(flagstaff_reader)},
    ).status_code == 403

    # Flagstaff owner configures Flagstaff/Sedona markets; Tucson owner configures Tucson/Oro Valley.
    r_flag = flagstaff_owner.post(
        cfg_url,
        json={"markets": ["Flagstaff, Arizona", "Sedona, Arizona"]},
        headers={"X-CSRF-Token": csrf_of(flagstaff_owner)},
    )
    assert r_flag.status_code == 200, r_flag.text[:250]
    assert r_flag.json()["config"]["markets"] == ["Flagstaff, Arizona", "Sedona, Arizona"]

    r_tuc = tucson_owner.post(
        cfg_url,
        json={"markets": ["Tucson, Arizona", "Oro Valley, Arizona"]},
        headers={"X-CSRF-Token": csrf_of(tucson_owner)},
    )
    assert r_tuc.status_code == 200, r_tuc.text[:250]
    assert r_tuc.json()["config"]["markets"] == ["Tucson, Arizona", "Oro Valley, Arizona"]

    # Flagstaff's GSC analysis classifies its own brand and maps its own cities.
    gsc_resp = flagstaff_owner.post(
        "/api/v1/modules/visibility/gsc/analyze",
        json={
            "csv_text": (
                "Query,Clicks,Impressions,Position\n"
                "flagstaff peak remodeling,3,24,1.2\n"
                "kitchen contractor sedona,1,18,5.4\n"
                "prescotts best construction,0,4,15.0\n"
            )
        },
        headers={"X-CSRF-Token": csrf_of(flagstaff_owner)},
    )
    assert gsc_resp.status_code == 200, gsc_resp.text[:250]
    rows = {r["query"]: r for r in gsc_resp.json()["snapshot"]["rows"]}
    assert rows["flagstaff peak remodeling"]["status"] == "brand"
    assert rows["kitchen contractor sedona"]["status"] == "eligible"
    assert rows["kitchen contractor sedona"]["geography"] == "Sedona, Arizona"
    assert rows["prescotts best construction"]["status"] == "eligible"

    # Flagstaff can plan its own markets, but not Tucson's or Prescott's markets.
    plan_ok = flagstaff_owner.post(
        "/api/v1/modules/visibility/plan",
        json={
            "questions": [{"id": "q-seed-1", "text": "Who remodels kitchens in Flagstaff?"}],
            "markets": ["Flagstaff, Arizona", "Sedona, Arizona"],
            "modes": ["as_written", "controlled_substitution"],
            "repetitions": 1,
            "chunk_size": 5,
            "max_total": 50,
        },
        headers={"X-CSRF-Token": csrf_of(flagstaff_owner)},
    )
    assert plan_ok.status_code == 200, plan_ok.text[:250]
    assert plan_ok.json()["request_count"] == 4

    for foreign_market in ("Tucson, Arizona", "Prescott Valley, Arizona"):
        plan_bad = flagstaff_owner.post(
            "/api/v1/modules/visibility/plan",
            json={
                "questions": [{"id": "q-seed-1", "text": "Who remodels kitchens?"}],
                "markets": [foreign_market],
                "modes": ["as_written"],
            },
            headers={"X-CSRF-Token": csrf_of(flagstaff_owner)},
        )
        assert plan_bad.status_code == 422

    # Tucson page renders Tucson markets and never leaks Flagstaff or Prescott configuration.
    tucson_page = tucson_owner.get("/app/modules/visibility").text
    assert 'value="Tucson, Arizona"' in tucson_page
    assert 'value="Oro Valley, Arizona"' in tucson_page
    assert "Flagstaff, Arizona" not in tucson_page
    assert "Prescott Valley, Arizona" not in tucson_page
    assert "Tucson Adobe Builders" in tucson_page


def test_pilot_preset_uses_only_configured_immutable_org_id():
    from uuid import uuid4
    pilot_id, other_id = uuid4(), uuid4()
    org = SimpleNamespace(id=other_id, name="Prescott's Best Construction", settings={"pilot_org": True, "visibility_lab": {"is_pilot": True}})
    for configured_id in (None, "", "invalid", str(pilot_id)):
        cfg = tenant_lab_config(org, pilot_org_id=configured_id)
        assert not cfg.is_pilot
        assert cfg.markets != MARKETS
        assert cfg.questions != BENCHMARK_22
    # Renaming a legitimately configured workspace does not change its stable identity.
    org.id = pilot_id
    org.name = "Renamed Synthetic Organization"
    assert tenant_lab_config(org, pilot_org_id=str(pilot_id)).is_pilot


def test_pilot_route_configuration_checks_actual_tenant_id(platform, monkeypatch):
    from .conftest import onboard_business
    a, org_a, _ = onboard_business(platform, business="Prescotts Best Construction", owner_email="pilot-identity-a@example.invalid")
    b, org_b, _ = onboard_business(platform, business="Prescott's Best Construction", owner_email="pilot-identity-b@example.invalid")
    assert org_a != org_b
    monkeypatch.setenv("MF_VISIBILITY_LAB_PILOT_ORG_ID", str(org_a))
    path = "/api/v1/modules/visibility/config"
    first = a.get(path)
    second = b.get(path)
    assert first.status_code == second.status_code == 200
    assert first.json()["config"]["is_pilot"] is True
    assert second.json()["config"]["is_pilot"] is False
    assert second.json()["config"]["markets"] != list(MARKETS)
