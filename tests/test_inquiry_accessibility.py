"""Synthetic Jinja2 accessibility and triage render tests for MF-002 inquiries template."""

from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
import re
from types import SimpleNamespace
from typing import Any

import pytest

from app.deps import BASE_DIR, templates


TEMPLATE_NAME = "app/inquiries.html"
APP_CSS_PATH = BASE_DIR / "static" / "app.css"
DEFAULT_STATUSES = ["new", "contacted", "qualified", "won", "lost", "spam"]
FIXED_TODAY = date(2026, 11, 11)


class SyntheticLead(dict):
    """Synthetic inquiry record supporting both attribute and dict-style access in Jinja2."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def make_lead(
    *,
    lead_id: int = 1,
    name: str = "Jordan Rivera",
    email: str = "jordan@example.com",
    service: str = "Kitchen Remodel",
    message: str = "Looking for an estimate next month.",
    status: str = "new",
    follow_up_on: date | None = None,
    created_at: datetime | None = None,
) -> SyntheticLead:
    """Build a synthetic inquiry record matching app/templates/app/inquiries.html expectations."""
    return SyntheticLead(
        id=lead_id,
        name=name,
        email=email,
        service=service,
        message=message,
        status=status,
        follow_up_on=follow_up_on,
        created_at=created_at or datetime(2026, 11, 10, 14, 30, 0),
    )


def render_inquiries(
    *,
    leads: list[SyntheticLead] | None = None,
    today: date = FIXED_TODAY,
    status: str | None = None,
    statuses: list[str] | None = None,
) -> str:
    """Render app/templates/app/inquiries.html using the real Jinja2 environment from app.deps."""
    template = templates.env.get_template(TEMPLATE_NAME)
    return template.render(
        org=SimpleNamespace(name="Synthetic Business", status="active"),
        brand="MeasuredField",
        role="owner",
        path="/app/inquiries",
        principal=SimpleNamespace(
            user=SimpleNamespace(
                is_platform_admin=False,
                name="Synthetic Owner",
                email="owner@example.invalid",
            )
        ),
        orgs=[],
        csrf="synthetic-csrf",
        flash="",
        flash_error="",
        leads=leads if leads is not None else [make_lead()],
        today=today,
        status=status,
        statuses=statuses if statuses is not None else list(DEFAULT_STATUSES),
    )


def get_inquiries_template_source() -> str:
    """Return raw source text of app/templates/app/inquiries.html from the Jinja2 loader."""
    loader = templates.env.loader
    assert loader is not None, "Expected templates.env.loader to be configured"
    source, _, _ = loader.get_source(templates.env, TEMPLATE_NAME)
    return source


def get_app_css_source() -> str:
    """Return raw source text of app/static/app.css served under style-src 'self'."""
    return APP_CSS_PATH.read_text(encoding="utf-8")


class _AccessibilityHTMLInspector(HTMLParser):
    """Lightweight HTML parser collecting labels, selects, table captions, and data-label cells."""

    def __init__(self) -> None:
        super().__init__()
        self.labels: list[dict[str, str | None]] = []
        self.selects: list[dict[str, str | None]] = []
        self.tables: int = 0
        self.captions: list[dict[str, Any]] = []
        self.td_data_labels: list[str] = []
        self.th_elements: int = 0
        self._in_caption = False
        self._current_caption_attrs: dict[str, str | None] = {}
        self._current_caption_text: list[str] = []
        self._in_label = False
        self._current_label_attrs: dict[str, str | None] = {}
        self._current_label_text: list[str] = []
        self._label_contains_select = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "label":
            self._in_label = True
            self._current_label_attrs = attr_map
            self._current_label_text = []
            self._label_contains_select = False
        elif tag == "select":
            if self._in_label:
                self._label_contains_select = True
            self.selects.append(attr_map)
        elif tag == "table":
            self.tables += 1
        elif tag == "caption":
            self._in_caption = True
            self._current_caption_attrs = attr_map
            self._current_caption_text = []
        elif tag == "th":
            self.th_elements += 1
        elif tag == "td" and attr_map.get("data-label"):
            self.td_data_labels.append(attr_map["data-label"] or "")

    def handle_data(self, data: str) -> None:
        if self._in_caption:
            self._current_caption_text.append(data)
        if self._in_label:
            self._current_label_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "caption" and self._in_caption:
            self._in_caption = False
            self.captions.append(
                {
                    "attrs": self._current_caption_attrs,
                    "text": "".join(self._current_caption_text).strip(),
                }
            )
        elif tag == "label" and self._in_label:
            self._in_label = False
            self.labels.append(
                {
                    "for": self._current_label_attrs.get("for"),
                    "class": self._current_label_attrs.get("class"),
                    "text": "".join(self._current_label_text).strip(),
                    "wraps_select": "true" if self._label_contains_select else "false",
                }
            )


def _inspect_html(html_text: str) -> _AccessibilityHTMLInspector:
    inspector = _AccessibilityHTMLInspector()
    inspector.feed(html_text)
    return inspector


def test_status_selector_has_accessible_label_and_options() -> None:
    """Status selector/filter control exposes an accessible label and renders provided statuses."""
    custom_statuses = ["new", "contacted", "proposal_sent", "won"]
    html = render_inquiries(
        leads=[make_lead(status="contacted")],
        today=FIXED_TODAY,
        status="contacted",
        statuses=custom_statuses,
    )
    inspector = _inspect_html(html)

    assert inspector.selects or inspector.labels, (
        "Expected status selector or labeled filter controls in rendered HTML"
    )

    label_for_ids = {lbl["for"] for lbl in inspector.labels if lbl.get("for") and lbl.get("text")}
    select_ids = {sel.get("id") for sel in inspector.selects if sel.get("id")}
    has_wrapped_select = any(
        lbl.get("wraps_select") == "true" and lbl.get("text") for lbl in inspector.labels
    )
    has_aria_labeled_select = any(
        bool(sel.get("aria-label") or sel.get("aria-labelledby")) for sel in inspector.selects
    )
    has_linked_label = bool(label_for_ids & select_ids)

    assert has_linked_label or has_wrapped_select or has_aria_labeled_select, (
        "Every status <select> should be associated with a non-empty <label> or aria-label"
    )

    for st in custom_statuses:
        assert st in html, f"Expected status option '{st}' to be rendered"


def test_screen_reader_table_caption_and_semantic_table() -> None:
    """Inquiries table includes a semantic <table>, <th> headers, and a non-empty <caption>."""
    html = render_inquiries(leads=[make_lead()])
    inspector = _inspect_html(html)

    assert inspector.tables >= 1, "Expected a semantic <table> element for inquiries"
    assert inspector.th_elements >= 3, "Expected semantic <th> table column headers"
    assert len(inspector.captions) >= 1, "Expected a <caption> inside the inquiries <table>"

    caption_texts = [cap["text"] for cap in inspector.captions if cap["text"]]
    assert caption_texts, "Expected non-empty descriptive text inside <caption> for screen readers"


def test_focus_visible_css_is_defined() -> None:
    """External stylesheet defines explicit :focus-visible styling for keyboard navigation accessibility."""
    source = get_inquiries_template_source()
    html = render_inquiries(leads=[make_lead()])
    css = get_app_css_source()
    combined = f"{source}\n{html}\n{css}"

    assert ":focus-visible" in combined, "Expected :focus-visible selector in external app.css"
    focus_match = re.search(
        r":focus-visible\s*\{[^}]*\b(outline|box-shadow)\b",
        combined,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert focus_match is not None, (
        "Expected :focus-visible rule to specify visible outline or box-shadow"
    )


@pytest.mark.parametrize(
    ("follow_up_on", "expected_state"),
    [
        (date(2026, 11, 1), "overdue"),
        (FIXED_TODAY, "due_today"),
        (None, "unscheduled"),
    ],
)
def test_overdue_vs_due_today_vs_unscheduled_triage(
    follow_up_on: date | None,
    expected_state: str,
) -> None:
    """Follow-up triage distinguishes overdue (< today), due today (== today), and unscheduled (None)."""
    lead = make_lead(
        lead_id=42,
        name=f"Lead {expected_state}",
        follow_up_on=follow_up_on,
    )
    html = render_inquiries(leads=[lead], today=FIXED_TODAY)

    tbody_match = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, flags=re.IGNORECASE | re.DOTALL)
    row_html = tbody_match.group(1) if tbody_match else html
    row_lower = row_html.lower()

    if expected_state == "overdue":
        assert "overdue" in row_lower, "Expected overdue indicator when follow_up_on < today"
        assert "2026-11-01" in row_html or "nov" in row_lower
    elif expected_state == "due_today":
        assert "today" in row_lower, "Expected due-today indicator when follow_up_on == today"
        assert "overdue" not in row_lower, "Due-today lead must not be marked overdue"
    else:
        assert "overdue" not in row_lower, "Unscheduled lead must not be marked overdue"
        assert "due today" not in row_lower and "due-today" not in row_lower, (
            "Unscheduled lead must not be marked due today"
        )
        assert any(
            token in row_lower
            for token in ("not set", "unscheduled", "not scheduled", "none", "—", "&mdash;")
        ), "Expected an explicit unscheduled/empty indicator when follow_up_on is None"


def test_responsive_data_label_cards_and_mobile_css() -> None:
    """Table cells include data-label attributes and responsive CSS rules for mobile card layout."""
    source = get_inquiries_template_source()
    html = render_inquiries(leads=[make_lead()])
    css = get_app_css_source()
    inspector = _inspect_html(html)

    assert len(inspector.td_data_labels) >= 4, (
        "Expected <td> cells to carry data-label attributes for responsive card view"
    )
    assert all(label.strip() for label in inspector.td_data_labels), (
        "All data-label attributes on <td> elements must be non-empty"
    )

    combined = f"{source}\n{html}\n{css}"
    assert "@media" in combined, "Expected responsive @media rule for mobile card layout"
    assert "data-label" in combined, "Expected data-label usage in template and stylesheet"


def test_no_inline_styles_and_external_css_contract() -> None:
    """Template has no inline <style> or style= attributes blocked by CSP style-src 'self', and external app.css provides all inquiry styles."""
    source = get_inquiries_template_source()
    html = render_inquiries(
        leads=[
            make_lead(lead_id=10, status="new", follow_up_on=FIXED_TODAY),
            make_lead(lead_id=20, status="contacted", follow_up_on=date(2026, 11, 1)),
        ],
        today=FIXED_TODAY,
    )
    css = get_app_css_source()

    assert "<style" not in source.lower(), "Inline <style> block is forbidden in inquiries.html under style-src 'self' CSP"
    assert "<style" not in html.lower(), "Rendered inquiries page must not include inline <style> tags"
    assert re.search(r"\bstyle\s*=", html, flags=re.IGNORECASE) is None, (
        "Rendered inquiries page must not include inline style= attributes"
    )
    assert '/static/app.css' in html, "Rendered inquiries page must link external /static/app.css"

    for required_css_token in (
        ".inquiries-desk",
        ".sr-only",
        ".triage-summary",
        ".inquiries-table",
        "@media (max-width: 720px)",
        "attr(data-label)",
    ):
        assert required_css_token in css, f"Expected '{required_css_token}' in app/static/app.css"

    # High-contrast filter submit button and direct-child header structure
    assert re.search(
        r'<form[^>]*class="status-filter-form"[^>]*>.*?<button[^>]*class="btn ghost small"[^>]*type="submit"[^>]*>Apply</button>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ), "Expected status filter Apply button to use high-contrast 'btn ghost small' class"

    # Overdue lead (id=20) should be prioritized over due-today lead (id=10) in jump link
    assert 'href="#lead-row-20"' in html, (
        "Expected jump link to prioritize overdue inquiry over due-today inquiry"
    )


def test_lead_fields_are_html_escaped() -> None:
    """Untrusted lead fields are HTML-escaped by Jinja2 autoescaping to prevent XSS injection."""
    malicious_lead = make_lead(
        lead_id=99,
        name='<script>alert("xss-name")</script>',
        email='bad"onclick="alert(1)@example.com',
        service='<img src=x onerror=alert("xss-service")>',
        message='<b>Urgent</b> & <iframe src="javascript:alert(1)"></iframe>',
        status='<svg onload=alert("xss-status")>',
    )
    html = render_inquiries(
        leads=[malicious_lead],
        today=FIXED_TODAY,
        status='<script>alert("filter")</script>',
        statuses=['new', '<script>alert("opt")</script>'],
    )

    assert '<script>alert("xss-name")</script>' not in html
    assert '<img src=x onerror=alert("xss-service")>' not in html
    assert '<iframe src="javascript:alert(1)"></iframe>' not in html
    assert '<svg onload=alert("xss-status")>' not in html
    assert '<script>alert("filter")</script>' not in html
    assert '<script>alert("opt")</script>' not in html

    assert "&lt;script&gt;alert(" in html
    assert "&lt;img src=x onerror=" in html
    assert "&lt;iframe" in html
