"""MF-053 owner-first deterministic business-growth launch plan.

This module coordinates real existing tools by their intended outcomes. It is
NOT a provider client, background worker, authorization layer, secret vault or
evidence that third-party accounts are connected. All user choices are
self-reported and cannot be treated as independently verified.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Industry = Literal["home_services", "professional", "retail", "hospitality", "other"]
Goal = Literal["website", "findability", "leads", "revenue", "automate"]
AiMode = Literal["gemini_free", "bring_your_own_key", "chatgpt", "manual"]
SiteState = Literal["no_website", "website_exists", "needs_improvement"]
Source = Literal["google_profile", "search_console", "website_forms", "customer_system"]


class GrowthJourneyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    industry: Industry
    business_name: str = Field(min_length=2, max_length=90)
    service_area: str = Field(min_length=2, max_length=100)
    website_state: SiteState
    goals: tuple[Goal, ...] = Field(min_length=1, max_length=5)
    ai_mode: AiMode = "gemini_free"
    # "I have an account" is not "account linked to MeasuredField".
    owner_has_accounts: tuple[Source, ...] = Field(default=(), max_length=4)

    @field_validator("business_name", "service_area")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("Please use ordinary printable business information")
        return value

    @field_validator("goals", "owner_has_accounts")
    @classmethod
    def unique_choices(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Please choose each option only once")
        return value


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    purpose: str
    action: str
    measurement: str
    effort: str
    link: str | None
    prerequisites: tuple[str, ...] = ()
    requires_owner_approval: bool = True
    mode: str = "existing_workspace_or_local_preview"


STEPS: dict[str, Step] = {
    "website": Step(
        "website", "Build a reliable website foundation",
        "A useful business site needs clear services, credibility and an easy request path.",
        "Review your website or create a private draft with a prominent call and estimate/request action.",
        "Qualified inquiry submissions, phone taps and form errors", "Medium",
        "/app/website",
    ),
    "profile": Step(
        "profile", "Own your Google Business Profile",
        "Local customers need correct business identity, categories and service areas.",
        "Verify ownership, service categories, hours, photos and policies in Google's actual profile console.",
        "Verified ownership, accurate business information and genuine customer feedback",
        "Low", None,
        ("website",),
        mode="manual_google_owner_action",
    ),
    "gsc": Step(
        "gsc", "Connect Search Console and validate indexing",
        "Your real Search Console evidence shows actual Google clicks and query exposure.",
        "Verify your property, review URL Inspection, robots and sitemap; submit an accurate sitemap for discovery.",
        "Index coverage, crawl errors, real clicks and impressions", "Low", "/app/modules/visibility",
        ("website",), mode="local_preview_plus_owner_google_action",
    ),
    "seo": Step(
        "seo", "Make each important page easy to understand",
        "Service pages need useful headings, readable content, accessible imagery and valid structured data.",
        "Inspect titles, headings, internal links, ALT text, service copy, canonical URLs and eligible schema. Prepare changes for review.",
        "Valid page checks, indexed pages, query impressions and relevant inquiry conversions",
        "Medium", "/app/website", ("website", "gsc"),
    ),
    "local": Step(
        "local", "Compare nearby markets and competitors",
        "Prescott Valley and Prescott may show different results for the same service.",
        "Import licensed, coordinate-specific SERP rank observations and compare matching phrases and search surfaces.",
        "Source-labelled ranking samples, city gaps and their repeatability",
        "Medium", "/app/modules/local-search", ("profile",),
        mode="owner_imported_local_preview_only",
    ),
    "funnel": Step(
        "funnel", "Track which page brings each inquiry",
        "A website should show which action and page generated a customer request.",
        "Review form entry points, referral/UTM sources, consent, spam controls and mobile call buttons; keep origins linked to inquiries.",
        "Submissions by originating page, qualified lead rate and response rate",
        "Medium", "/app/inquiries", ("website",),
    ),
    "followup": Step(
        "followup", "Recover inquiries before they go cold",
        "Customers who do not receive timely responses are easily lost.",
        "Review your inquiry desk, prioritize unanswered requests and prepare follow-ups for your approval.",
        "First response time, reply coverage and estimates booked", "Low",
        "/app/lead-rescue", ("funnel",),
    ),
    "revenue": Step(
        "revenue", "Test a new service or revenue opportunity",
        "New offers should fit your actual customers, skills, seasonal demand and capacity.",
        "Use observed questions and inquiries to outline one offer, a cost hypothesis and a limited test before announcing it.",
        "Offer inquiries, estimate-to-sale conversion and contribution margin",
        "Medium", "/app/improvements", ("funnel",),
    ),
    "improve": Step(
        "improve", "Turn measurements into the next approved change",
        "Rank, clicks and inquiries matter only if you decide what to do and learn from the result.",
        "Pick one evidence-backed improvement, review its draft, approve it and compare before/after outcomes.",
        "Completed approved changes and qualified results over time",
        "Medium", "/app/improvements", ("seo", "followup"),
    ),
    "connect": Step(
        "connect", "Choose your AI and approved account connections",
        "Useful automation depends on permissioned access and protected customer information.",
        "Review the provider choice, data privacy and intended scopes. Use owner consent before any connection or modification.",
        "Authenticated connection state, approved actions and revocation checks",
        "Low", "/app/connections",
        mode="private_connectivity_pilot",
    ),
}

INDUSTRY_EXAMPLES = {
    "home_services": (
        "Bathrooms, kitchens, carpentry or other qualifying service niches",
        "Estimate requests, calls, completed jobs and homeowner reviews",
    ),
    "professional": (
        "Specialized consultations, educational content and referral partnerships",
        "Qualified consultations, referrals and client conversions",
    ),
    "retail": (
        "Bundled offerings, seasonal promotions and returning-customer campaigns",
        "Product inquiries, conversion and repeat purchase",
    ),
    "hospitality": (
        "Seasonal packages, group/event inquiries and customer retention",
        "Bookings, qualified inquiries and repeat visits",
    ),
    "other": (
        "Underserved client needs, repeat purchases and adjacent offers",
        "Qualified leads, completed sales and retention",
    ),
}
PROVIDER_MODES: dict[str, dict] = {
    "gemini_free": {
        "title": "Gemini starter (free tier when available)",
        "configuration": "not_verified",
        "credential_required": "An eligible, owner-approved Gemini project or platform entitlement.",
        "privacy": "Google's free tier may use submitted content to improve its products; do not send private lead/customer data there by default.",
        "cost": "Free allowance subject to model, project and rate limits; availability is not guaranteed.",
        "what_works_now": "The current preview produces a useful deterministic plan without contacting Gemini.",
    },
    "bring_your_own_key": {
        "title": "Use your own model API key",
        "configuration": "future_secure_vault_required",
        "credential_required": "A provider account and a tenant-isolated, encrypted credentials vault after security review.",
        "privacy": "Key entry is deliberately disabled in this preview. Never paste API secrets into notes or chat.",
        "cost": "The provider bills your account according to the selected model and features; geographic SERP calls are separate.",
        "what_works_now": "Plan tools and account connection checklist are available; BYOK model execution is not enabled.",
    },
    "chatgpt": {
        "title": "Connect MeasuredField to ChatGPT",
        "configuration": "real_oauth_and_plugin_install_not_verified",
        "credential_required": "A supported ChatGPT plan/workspace, installed app and actual owner OAuth consent.",
        "privacy": "The linked app sees only authorized MeasuredField data and tools; file edits require separate approval.",
        "cost": "ChatGPT subscriptions and ordinary API billing are separate; some eligible integrations support allowance sharing with explicit consent.",
        "what_works_now": "Connection controls and protocol tests exist; live ChatGPT-to-MeasuredField setup is not yet completed.",
    },
    "manual": {
        "title": "Start without any AI account",
        "configuration": "no_external_provider_required",
        "credential_required": "None for deterministic planning and reviewing your own project materials.",
        "privacy": "This preview sends no content to an AI model or search provider.",
        "cost": "No third-party AI or SERP usage is triggered by this plan.",
        "what_works_now": "The step-by-step growth plan and internal workspace tool links work without any model.",
    },
}


def plan_growth(inputs: GrowthJourneyInput) -> dict:
    if not isinstance(inputs, GrowthJourneyInput):
        inputs = GrowthJourneyInput.model_validate(inputs)
    goals = set(inputs.goals)
    requested = ["connect"]
    if inputs.website_state != "website_exists" or "website" in goals or "findability" in goals or "leads" in goals:
        requested.append("website")
    if "findability" in goals or "website" in goals:
        requested += ["profile", "gsc", "seo", "local"]
    if "leads" in goals or "revenue" in goals:
        requested += ["funnel", "followup"]
    if "revenue" in goals:
        requested.append("revenue")
    if "automate" in goals:
        requested += ["funnel", "followup", "improve"]
    if "website" in goals or "findability" in goals or "leads" in goals or "automate" in goals:
        requested.append("improve")
    # Preserve logical order and attach prerequisites as explanatory needs;
    # never imply a prerequisite account was already connected.
    requested = list(dict.fromkeys(requested))
    order = ("website", "profile", "gsc", "seo", "local", "funnel", "followup", "revenue", "improve", "connect")
    ordered = [name for name in order if name in requested]
    steps = []
    for number, key in enumerate(ordered, 1):
        step = STEPS[key]
        source = {
            "profile": "google_profile",
            "gsc": "search_console",
            "funnel": "website_forms",
        }.get(key)
        owner_reported = source in inputs.owner_has_accounts if source else None
        steps.append({
            "number": number, "id": key, "title": step.title,
            "why": step.purpose, "recommended_action": step.action,
            "measure": step.measurement, "effort": step.effort, "link": step.link,
            "prerequisites": [STEPS[p].title for p in step.prerequisites],
            "owner_reports_existing_account": owner_reported,
            "account_connection_verified": False,
            "execution_status": "not_started", "requires_owner_approval": step.requires_owner_approval,
            "implementation_stage": step.mode,
        })
    idea, outcome = INDUSTRY_EXAMPLES[inputs.industry]
    provider = PROVIDER_MODES[inputs.ai_mode]
    return {
        "status": "local_planning_preview_no_automated_actions",
        "business": {"name": inputs.business_name, "service_area": inputs.service_area,
                     "industry": inputs.industry, "website_state": inputs.website_state},
        "selected_goals": list(inputs.goals),
        "provider": {"id": inputs.ai_mode, **provider, "key_entry_enabled": False,
                     "model_calls_executed": 0, "provider_connected_verified": False},
        "industry_opportunity": {
            "hypothesis": idea, "measure": outcome,
            "provenance": "industry_example_not_customer_evidence",
        },
        "steps": steps, "planned_steps": len(steps),
        "permissions": {
            "account_connections_verified": False, "website_publication_enabled": False,
            "automatic_messages_enabled": False, "paid_research_enabled": False,
            "desktop_edit_access_enabled": False,
        },
        "evidence_limits": [
            "Account ownership and connected status were not independently verified.",
            "This plan is an initial recommendation, not proof of website improvement or revenue.",
            "Submitting an ordinary business sitemap does not guarantee Google indexing.",
            "Google's Indexing API is not an indexing shortcut for ordinary remodeling/service pages.",
            "Owner approval, actual source evidence and follow-up measurement precede publishing or spending.",
        ],
    }
