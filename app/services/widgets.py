"""Deterministic, accessible Scope & Readiness Explorer widget generator.

Produces pure HTML and externalized CSS (no inline ``style`` attributes, no inline event
handlers, no fabricated prices or labor estimates) compatible with ``APP_CSP`` and
``SITE_CSP`` (``style-src 'self'; script-src 'self'``).
"""
from __future__ import annotations

import html
from typing import Sequence

from ..schemas.preview import (
    WidgetParameter,
    WidgetParameterOption,
    WidgetSpec,
    normalize_industry,
)


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


WIDGET_CSS = """
.mf-widgets-section{background:#f4f9f8;border-top:1px solid #dce9e7;border-bottom:1px solid #dce9e7;padding:64px 0}
.mf-widget-card{background:#fff;border:1px solid #c5dbd7;border-radius:14px;padding:28px;margin-top:20px}
.mf-widget-card h3{margin:0 0 8px;font-size:1.35rem;color:#18353c}
.mf-widget-intro{margin:0 0 20px;color:#4d6669;max-width:720px}
.mf-widget-fieldset{border:0;margin:0;padding:0}
.mf-widget-legend{font-weight:750;font-size:1.02rem;color:#18353c;margin-bottom:12px;padding:0}
.mf-widget-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-bottom:22px}
.mf-widget-field label{display:block;font-weight:700;margin:0 0 6px;color:#1b343a}
.mf-widget-help{display:block;font-size:.84rem;color:#536a6d;margin-bottom:6px}
.mf-widget-summary{background:#edf5f2;border-left:4px solid var(--accent);border-radius:8px;padding:16px 18px;margin:18px 0}
.mf-widget-summary h4{margin:0 0 8px;font-size:1rem;color:#18353c}
.mf-widget-summary ul{margin:0 0 10px;padding-left:20px;color:#1b343a}
.mf-widget-summary p{margin:0;font-size:.9rem;color:#4d6669}
.mf-widget-actions{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:14px}
.mf-widget-status{font-size:.9rem;font-weight:650;color:#286854}
.mf-widget-disclaimer{font-size:.84rem;color:#536a6d;margin-top:14px}
@media(max-width:720px){.mf-widget-grid{grid-template-columns:1fr}.mf-widget-card{padding:20px}}
""".strip()


_INDUSTRY_WIDGET_BUILDERS: dict[str, dict] = {
    "construction": {
        "heading": "Project Scope & Site Readiness Explorer",
        "intro": (
            "Select the type of work, current site readiness, and desired timing to organize "
            "a clear project summary before requesting an owner walk-through."
        ),
        "parameters": [
            WidgetParameter(
                key="project_focus",
                label="What kind of project are you planning?",
                help_text="Helps match your request to the right crew and trade scope.",
                options=[
                    WidgetParameterOption(
                        value="interior_remodel",
                        label="Kitchen, bath, or interior remodel",
                        scope_note="Interior remodeling scope; on-site measurement and fixture review recommended.",
                    ),
                    WidgetParameterOption(
                        value="addition_structural",
                        label="Room addition or structural modification",
                        scope_note="Addition or structural scope; plans and property setbacks to be reviewed with owner.",
                    ),
                    WidgetParameterOption(
                        value="exterior_repair",
                        label="Exterior siding, deck, or repair work",
                        scope_note="Exterior improvement scope; site access and material options to be confirmed.",
                    ),
                ],
            ),
            WidgetParameter(
                key="site_readiness",
                label="Where are you in the planning process?",
                help_text="Lets the builder know what information is ready to review.",
                options=[
                    WidgetParameterOption(
                        value="ideas_and_photos",
                        label="Have rough measurements or photos ready",
                        scope_note="Initial concept stage with reference photos or room dimensions available.",
                    ),
                    WidgetParameterOption(
                        value="drawings_ready",
                        label="Have written scope or architectural drawings",
                        scope_note="Written scope or drawings available for detailed owner review.",
                    ),
                    WidgetParameterOption(
                        value="need_walkthrough",
                        label="Need an initial on-site walk-through",
                        scope_note="Requesting an initial site visit to discuss options and constraints.",
                    ),
                ],
            ),
            WidgetParameter(
                key="target_window",
                label="When would you like to begin?",
                help_text="Actual schedule depends on current crew availability and material lead times.",
                options=[
                    WidgetParameterOption(
                        value="within_30_days",
                        label="Within the next 30 days",
                        scope_note="Target start window: within 30 days (subject to schedule confirmation).",
                    ),
                    WidgetParameterOption(
                        value="one_to_three_months",
                        label="In 1 to 3 months",
                        scope_note="Target start window: 1 to 3 months out.",
                    ),
                    WidgetParameterOption(
                        value="flexible_planning",
                        label="Flexible / gathering scope details",
                        scope_note="Timeline is flexible; organizing project requirements first.",
                    ),
                ],
            ),
        ],
    },
    "cleaning": {
        "heading": "Cleaning Scope & Property Readiness Explorer",
        "intro": (
            "Outline the space, cleaning type, and scheduling preference so we can confirm "
            "availability and checklist details."
        ),
        "parameters": [
            WidgetParameter(
                key="service_type",
                label="Which type of cleaning do you need?",
                help_text="Select the visit format that best fits your space.",
                options=[
                    WidgetParameterOption(
                        value="recurring_care",
                        label="Recurring home or office upkeep",
                        scope_note="Recurring cleaning cadence (weekly, biweekly, or monthly options).",
                    ),
                    WidgetParameterOption(
                        value="deep_clean",
                        label="One-time deep cleaning",
                        scope_note="Detailed top-to-bottom cleaning for kitchens, baths, and living areas.",
                    ),
                    WidgetParameterOption(
                        value="move_transition",
                        label="Move-in or move-out preparation",
                        scope_note="Empty-property turnover cleaning prior to move-in or handover.",
                    ),
                ],
            ),
            WidgetParameter(
                key="property_profile",
                label="How would you describe the space?",
                help_text="Helps plan supplies and team size during owner review.",
                options=[
                    WidgetParameterOption(
                        value="compact_residence",
                        label="Apartment, condo, or smaller home (1–2 bedrooms)",
                        scope_note="Compact residential layout (1–2 bedrooms).",
                    ),
                    WidgetParameterOption(
                        value="family_residence",
                        label="Multi-bedroom home (3+ bedrooms)",
                        scope_note="Larger residential layout (3+ bedrooms or multiple levels).",
                    ),
                    WidgetParameterOption(
                        value="commercial_suite",
                        label="Local office, studio, or retail suite",
                        scope_note="Commercial workspace or customer-facing suite.",
                    ),
                ],
            ),
            WidgetParameter(
                key="access_readiness",
                label="What is your preferred timing?",
                help_text="We confirm exact arrival windows directly with you.",
                options=[
                    WidgetParameterOption(
                        value="this_week",
                        label="This week if a slot is open",
                        scope_note="Looking for near-term availability this week.",
                    ),
                    WidgetParameterOption(
                        value="next_two_weeks",
                        label="Within the next two weeks",
                        scope_note="Preferred scheduling window within two weeks.",
                    ),
                    WidgetParameterOption(
                        value="planning_ahead",
                        label="Planning ahead for an upcoming date",
                        scope_note="Advance scheduling for a specific future date.",
                    ),
                ],
            ),
        ],
    },
    "professional": {
        "heading": "Engagement Scope & Preparation Explorer",
        "intro": (
            "Share your primary objective, current documentation state, and timeline so our "
            "first conversation focuses on what matters most."
        ),
        "parameters": [
            WidgetParameter(
                key="engagement_goal",
                label="What is the primary focus of your request?",
                help_text="Helps route your inquiry to the right specialist.",
                options=[
                    WidgetParameterOption(
                        value="focused_review",
                        label="One-time review or Second Opinion",
                        scope_note="Focused review of a specific question, document, or decision.",
                    ),
                    WidgetParameterOption(
                        value="project_engagement",
                        label="Defined project with clear milestones",
                        scope_note="Project-based engagement with agreed deliverables.",
                    ),
                    WidgetParameterOption(
                        value="ongoing_advisory",
                        label="Ongoing monthly or quarterly support",
                        scope_note="Recurring advisory relationship and periodic check-ins.",
                    ),
                ],
            ),
            WidgetParameter(
                key="materials_ready",
                label="What background records do you have ready?",
                help_text="Do not send confidential files until we open your private workspace.",
                options=[
                    WidgetParameterOption(
                        value="records_organized",
                        label="Prior records and summary notes are ready",
                        scope_note="Existing documentation is organized and ready to share privately.",
                    ),
                    WidgetParameterOption(
                        value="partial_records",
                        label="Some documents ready, need guidance on gaps",
                        scope_note="Partial records available; checklist needed before onboarding.",
                    ),
                    WidgetParameterOption(
                        value="starting_fresh",
                        label="Starting fresh / new business or matter",
                        scope_note="New initiative; starting from an initial discovery conversation.",
                    ),
                ],
            ),
            WidgetParameter(
                key="timeline_urgency",
                label="When do you need to get started?",
                help_text="Let us know if you are working toward an external deadline.",
                options=[
                    WidgetParameterOption(
                        value="upcoming_deadline",
                        label="Working toward a near-term deadline",
                        scope_note="Time-sensitive request with an upcoming target date.",
                    ),
                    WidgetParameterOption(
                        value="this_month",
                        label="Looking to begin this month",
                        scope_note="Target onboarding window within the current month.",
                    ),
                    WidgetParameterOption(
                        value="exploratory",
                        label="Exploratory conversation first",
                        scope_note="Early-stage planning; comparing options and fit.",
                    ),
                ],
            ),
        ],
    },
    "retail": {
        "heading": "Order & Availability Scope Explorer",
        "intro": (
            "Select what you are looking for, quantity needs, and fulfillment preference so "
            "we can check current stock or custom options."
        ),
        "parameters": [
            WidgetParameter(
                key="request_kind",
                label="What are you inquiring about?",
                help_text="Choose the option that best matches your product question.",
                options=[
                    WidgetParameterOption(
                        value="in_stock_hold",
                        label="Check item availability or local hold",
                        scope_note="Checking current in-store availability for specific items.",
                    ),
                    WidgetParameterOption(
                        value="custom_selection",
                        label="Custom order or special configuration",
                        scope_note="Custom specification or special-order item inquiry.",
                    ),
                    WidgetParameterOption(
                        value="bulk_business",
                        label="Group, event, or business order",
                        scope_note="Multi-unit or event order requiring stock coordination.",
                    ),
                ],
            ),
            WidgetParameter(
                key="quantity_tier",
                label="Approximate quantity or order scope",
                help_text="Helps us check shelf stock vs. supplier lead time.",
                options=[
                    WidgetParameterOption(
                        value="single_item",
                        label="Individual item or small selection",
                        scope_note="Individual or small-batch selection.",
                    ),
                    WidgetParameterOption(
                        value="small_batch",
                        label="Several items for a project or gift set",
                        scope_note="Multi-item bundle or coordinated selection.",
                    ),
                    WidgetParameterOption(
                        value="larger_lot",
                        label="Larger quantity for an organization or event",
                        scope_note="Larger quantity request; lead time to be confirmed by owner.",
                    ),
                ],
            ),
            WidgetParameter(
                key="fulfillment_preference",
                label="How would you like to receive your order?",
                help_text="We will confirm pickup hours or delivery coverage in our reply.",
                options=[
                    WidgetParameterOption(
                        value="local_pickup",
                        label="In-person pickup at the shop",
                        scope_note="Prefers local in-store pickup.",
                    ),
                    WidgetParameterOption(
                        value="local_delivery",
                        label="Local delivery (if available in service area)",
                        scope_note="Interested in local delivery options within the service area.",
                    ),
                    WidgetParameterOption(
                        value="discuss_options",
                        label="Open to either pickup or shipping options",
                        scope_note="Flexible on fulfillment method.",
                    ),
                ],
            ),
        ],
    },
}

_DEFAULT_OTHER_BUILDER: dict = {
    "heading": "Service Scope & Readiness Explorer",
    "intro": (
        "Select your primary need, current stage, and preferred timing to prepare a clear "
        "request summary."
    ),
    "parameters": [
        WidgetParameter(
            key="service_need",
            label="What kind of help are you looking for?",
            help_text="Gives the owner context before replying.",
            options=[
                WidgetParameterOption(
                    value="standard_service",
                    label="Standard service request",
                    scope_note="Interested in core services listed on this page.",
                ),
                WidgetParameterOption(
                    value="custom_project",
                    label="Custom project or tailored request",
                    scope_note="Custom requirement that benefits from a quick scope call.",
                ),
                WidgetParameterOption(
                    value="general_question",
                    label="Availability and service-area question",
                    scope_note="Checking service area coverage and general availability.",
                ),
            ],
        ),
        WidgetParameter(
            key="readiness_stage",
            label="What details do you have ready?",
            help_text="Helps streamline follow-up.",
            options=[
                WidgetParameterOption(
                    value="ready_to_schedule",
                    label="Scope is clear and ready to schedule",
                    scope_note="Details are ready; looking to confirm next available steps.",
                ),
                WidgetParameterOption(
                    value="comparing_options",
                    label="Have a rough idea, want to discuss options",
                    scope_note="Looking for owner guidance on the best approach.",
                ),
            ],
        ),
        WidgetParameter(
            key="preferred_timing",
            label="When are you hoping to start?",
            help_text="Subject to owner schedule confirmation.",
            options=[
                WidgetParameterOption(
                    value="this_week",
                    label="This week",
                    scope_note="Preferred timing: this week.",
                ),
                WidgetParameterOption(
                    value="this_month",
                    label="Within this month",
                    scope_note="Preferred timing: within the month.",
                ),
                WidgetParameterOption(
                    value="planning",
                    label="Just planning ahead",
                    scope_note="Preferred timing: planning ahead.",
                ),
            ],
        ),
    ],
}


def default_widget_for_industry(industry: str | None) -> WidgetSpec:
    """Return a deterministic, validated ``WidgetSpec`` for ``industry``."""
    norm = normalize_industry(industry)
    template = _INDUSTRY_WIDGET_BUILDERS.get(norm, _DEFAULT_OTHER_BUILDER)
    return WidgetSpec(
        widget_id="scope_readiness_explorer",
        version="1.0",
        industry=norm,
        heading=template["heading"],
        intro=template["intro"],
        parameters=template["parameters"],
    )


def render_widgets_html(widgets: Sequence[WidgetSpec]) -> str:
    """Render accessible, CSP-safe HTML for one or more validated ``WidgetSpec`` objects."""
    if not widgets:
        return ""

    blocks: list[str] = []
    for idx, raw_widget in enumerate(widgets):
        spec = raw_widget if isinstance(raw_widget, WidgetSpec) else WidgetSpec.model_validate(raw_widget)
        heading_id = f"mf-widget-title-{idx}"
        summary_id = f"mf-widget-summary-{idx}"

        controls_html: list[str] = []
        default_notes: list[str] = []

        for param in spec.parameters:
            select_id = f"mf-param-{idx}-{_e(param.key)}"
            help_id = f"mf-help-{idx}-{_e(param.key)}"
            options_html: list[str] = []
            for opt_idx, opt in enumerate(param.options):
                if opt_idx == 0:
                    default_notes.append(
                        f"<li><strong>{_e(param.label)}</strong> {_e(opt.label)} — {_e(opt.scope_note)}</li>"
                    )
                options_html.append(
                    f'<option value="{_e(opt.value)}" data-mf-note="{_e(opt.scope_note)}">'
                    f"{_e(opt.label)}</option>"
                )
            help_span = (
                f'<span class="mf-widget-help" id="{help_id}">{_e(param.help_text)}</span>'
                if param.help_text
                else ""
            )
            aria_desc = f' aria-describedby="{help_id}"' if param.help_text else ""
            controls_html.append(
                f'<div class="mf-widget-field">'
                f'<label for="{select_id}">{_e(param.label)}</label>'
                f"{help_span}"
                f'<select id="{select_id}" name="widget_{_e(param.key)}" '
                f'data-mf-param="{_e(param.key)}" data-mf-param-label="{_e(param.label)}"{aria_desc}>'
                f'{"".join(options_html)}'
                f"</select></div>"
            )

        blocks.append(
            f'<article class="mf-widget-card" data-mf-widget="scope-explorer" '
            f'data-mf-industry="{_e(spec.industry)}">'
            f'<div class="eyebrow">Interactive Concept Demo</div>'
            f'<h3 id="{heading_id}">{_e(spec.heading)}</h3>'
            f'<p class="mf-widget-intro">{_e(spec.intro)}</p>'
            f'<fieldset class="mf-widget-fieldset">'
            f'<legend class="mf-widget-legend">Select your project parameters</legend>'
            f'<div class="mf-widget-grid">{"".join(controls_html)}</div>'
            f"</fieldset>"
            f'<div class="mf-widget-summary" id="{summary_id}" role="region" '
            f'aria-live="polite" aria-label="Selected project scope summary" data-mf-scope-summary="true">'
            f"<h4>{_e(spec.summary_heading)}</h4>"
            f'<ul data-mf-summary-list="true">{"".join(default_notes)}</ul>'
            f"<p>Concept preview summary — no pricing, labor hours, or permit determinations are estimated.</p>"
            f"</div>"
            f'<div class="mf-widget-actions">'
            f'<button type="button" class="btn mf-widget-apply" data-mf-scope-apply="true">'
            f"Copy summary into sample inquiry below</button>"
            f'<span class="mf-widget-status" role="status" aria-live="polite" data-mf-widget-status="true"></span>'
            f"</div>"
            f'<p class="mf-widget-disclaimer">{_e(spec.disclaimer)}</p>'
            f"</article>"
        )

    return (
        '<section id="scope-explorer" class="mf-widgets-section" aria-labelledby="mf-widget-title-0">'
        '<div class="wrap">'
        + "".join(blocks)
        + "</div></section>"
    )
