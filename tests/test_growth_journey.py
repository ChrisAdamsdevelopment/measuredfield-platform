"""MF-053 owner growth journey, AI option boundaries and authenticated preview."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from app.services.growth_journey import GrowthJourneyInput, plan_growth


def setup(**changes):
    values = {
        "industry": "home_services",
        "business_name": "Synthetic Remodeler",
        "service_area": "Prescott Valley and Prescott",
        "website_state": "needs_improvement",
        "goals": ("website", "findability", "leads", "revenue", "automate"),
        "ai_mode": "gemini_free",
        "owner_has_accounts": ("google_profile", "search_console"),
    }
    values.update(changes)
    return GrowthJourneyInput.model_validate(values)


def test_complete_business_growth_plan_connects_real_workspace_tools():
    plan = plan_growth(setup())
    ids = [step["id"] for step in plan["steps"]]
    assert plan["planned_steps"] == 10
    assert ids == [
        "website", "profile", "gsc", "seo", "local", "funnel",
        "followup", "revenue", "improve", "connect",
    ]
    assert plan["provider"]["model_calls_executed"] == 0
    assert plan["provider"]["key_entry_enabled"] is False
    assert plan["permissions"]["website_publication_enabled"] is False
    assert plan["permissions"]["automatic_messages_enabled"] is False
    assert plan["permissions"]["paid_research_enabled"] is False
    assert plan["business"]["service_area"] == "Prescott Valley and Prescott"
    assert all(step["why"] and step["recommended_action"] and step["measure"]
               and step["effort"] and step["requires_owner_approval"] is True
               for step in plan["steps"])
    assert all(step["execution_status"] == "not_started" for step in plan["steps"])
    assert all(step["account_connection_verified"] is False for step in plan["steps"])


@pytest.mark.parametrize("mode", ["gemini_free", "bring_your_own_key", "chatgpt", "manual"])
def test_all_ai_modes_have_truthful_limits_without_key_entry(mode):
    plan = plan_growth(setup(ai_mode=mode, goals=("website",)))
    provider = plan["provider"]
    assert provider["id"] == mode
    assert provider["key_entry_enabled"] is False
    assert provider["model_calls_executed"] == 0
    assert provider["provider_connected_verified"] is False
    assert provider["credential_required"]
    assert provider["privacy"]
    assert provider["cost"]
    assert plan["permissions"]["paid_research_enabled"] is False


def test_gemini_free_never_describes_private_lead_data_as_automatically_safe():
    result = plan_growth(setup(ai_mode="gemini_free"))
    assert "improve its products" in result["provider"]["privacy"]
    assert "private lead/customer data" in result["provider"]["privacy"]
    assert "not guaranteed" in result["provider"]["cost"].lower()


def test_byok_does_not_accept_or_reveal_an_api_key():
    data = setup(ai_mode="bring_your_own_key")
    with pytest.raises(ValidationError, match="Extra inputs"):
        GrowthJourneyInput.model_validate({
            **data.model_dump(), "api_key": "synthetic-private-credential",
        })
    payload = plan_growth(data)
    assert "synthetic-private-credential" not in str(payload)
    assert "vault" in payload["provider"]["configuration"]
    assert not payload["provider"]["key_entry_enabled"]


def test_chatgpt_is_an_optional_interface_not_required_for_growth_plan():
    with_chatgpt = plan_growth(setup(ai_mode="chatgpt"))
    without = plan_growth(setup(ai_mode="manual"))
    assert [x["id"] for x in with_chatgpt["steps"]] == [x["id"] for x in without["steps"]]
    assert with_chatgpt["provider"]["configuration"] != "connected"
    assert "live ChatGPT-to-MeasuredField setup is not yet completed" in (
        with_chatgpt["provider"]["what_works_now"]
    )


@pytest.mark.parametrize("goals,steps", [
    (("website",), {"website", "profile", "gsc", "seo", "local", "improve", "connect"}),
    (("leads",), {"website", "funnel", "followup", "improve", "connect"}),
    (("revenue",), {"funnel", "followup", "revenue", "connect"}),
    (("automate",), {"funnel", "followup", "improve", "connect"}),
])
def test_personalized_priorities_are_relevant_to_selected_goals(goals, steps):
    result = plan_growth(setup(goals=goals, website_state="website_exists"))
    assert set(step["id"] for step in result["steps"]) == steps


def test_new_business_starts_with_website_before_lead_funnel():
    result = plan_growth(setup(
        website_state="no_website", goals=("leads", "revenue"),
    ))
    ids = [step["id"] for step in result["steps"]]
    assert ids.index("website") < ids.index("funnel") < ids.index("followup")
    assert ids.index("followup") < ids.index("revenue")


def test_account_checkboxes_do_not_assert_any_provider_connected():
    result = plan_growth(setup(owner_has_accounts=(
        "google_profile", "search_console", "website_forms", "customer_system",
    )))
    observed = {step["id"]: step for step in result["steps"]}
    assert observed["profile"]["owner_reports_existing_account"] is True
    assert observed["gsc"]["owner_reports_existing_account"] is True
    assert observed["funnel"]["owner_reports_existing_account"] is True
    assert result["permissions"]["account_connections_verified"] is False
    assert all(not step["account_connection_verified"] for step in result["steps"])


@pytest.mark.parametrize("bad", [
    {"industry": "accounting_invasion"},
    {"ai_mode": "unrestricted_shell"},
    {"website_state": "deployed"},
    {"goals": ()},
    {"goals": ("leads", "leads")},
    {"goals": ("leads", "anything_else")},
    {"owner_has_accounts": ("search_console", "search_console")},
    {"business_name": "<script>\nmalicious()</script>"},
])
def test_unknown_or_duplicate_choices_are_rejected(bad):
    with pytest.raises(ValidationError):
        setup(**bad)


def test_plan_is_stable_deterministic_and_has_no_external_execution():
    a = setup()
    assert plan_growth(a) == plan_growth(a)
    assert plan_growth(a)["status"] == "local_planning_preview_no_automated_actions"
    assert any("Indexing API" in s for s in plan_growth(a)["evidence_limits"])
    assert not plan_growth(a)["permissions"]["desktop_edit_access_enabled"]


def test_private_template_works_with_server_csrf_and_safe_owner_copy():
    root = Path(__file__).resolve().parents[1]
    templates = Environment(
        loader=FileSystemLoader(str(root / "app/templates")),
        autoescape=select_autoescape(["html"]),
    )
    principal = SimpleNamespace(user=SimpleNamespace(
        name="Synthetic Owner", email="demo@example.invalid", is_platform_admin=False))
    html = templates.get_template("app/growth_journey.html").render(
        org=SimpleNamespace(name="Example Co", status="active"),
        role="owner", principal=principal, orgs=[], brand="MeasuredField",
        settings=SimpleNamespace(is_production=False),
        path="/app/growth-start", flash="", flash_error="",
        csrf="synthetic-csrf", request=object(),
    )
    assert 'id="gj-form"' in html
    assert 'data-csrf="synthetic-csrf"' in html
    assert 'aria-live="polite"' in html
    assert "Build my growth plan" in html
    assert "encrypted key connection is not enabled yet" in html
    assert "No account links, emails, website changes" in html
    assert 'href="/app/growth-start"' in html


def test_authenticated_growth_endpoint_obeys_csrf_and_dev_only(platform):
    from .conftest import client_for, csrf_of, onboard_business

    owner, _, _ = onboard_business(
        platform, business="Synthetic Studio", owner_email="mf053-owner@example.invalid")
    assert owner.get("/app/growth-start").status_code == 200
    url = "/api/v1/growth-start/plan"
    payload = setup().model_dump()
    assert owner.post(url, json=payload).status_code == 403
    result = owner.post(url, json=payload, headers={"X-CSRF-Token": csrf_of(owner)})
    assert result.status_code == 200, result.text[:300]
    assert result.json()["planned_steps"] == 10
    assert result.json()["provider"]["key_entry_enabled"] is False
    assert result.json()["permissions"]["paid_research_enabled"] is False
    bad = {**payload, "api_key": "secret-should-never-store"}
    invalid = owner.post(url, json=bad, headers={"X-CSRF-Token": csrf_of(owner)})
    assert invalid.status_code == 422
    assert "secret-should-never-store" not in invalid.text
    anonymous = client_for(platform)
    assert anonymous.get("/app/growth-start").status_code == 303
    assert anonymous.post(url, json=payload).status_code in (401, 403)
