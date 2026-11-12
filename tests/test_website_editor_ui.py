"""Synthetic Jinja2 UI, workflow, CSP, and accessibility regression tests for MF-007 website.html."""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import re
from types import SimpleNamespace
from typing import Any

import pytest

from app.deps import BASE_DIR, templates


TEMPLATE_NAME = "app/website.html"
APP_CSS_PATH = BASE_DIR / "static" / "app.css"

DEFAULT_INDUSTRIES = ["cleaning", "remodeling", "landscaping", "plumbing", "electrical"]
DEFAULT_COLORS = ["forest", "slate", "copper", "navy"]
DEFAULT_WINDOWS = {
    "same_day": "Within the same business day",
    "next_day": "Within 1 business day",
    "two_days": "Within 2 business days",
}


class SyntheticRecord(dict):
    """Synthetic object supporting both attribute and dict access in Jinja2 templates."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def make_site(
    *,
    site_id: int = 1,
    slug: str = "prescott-clean",
    published_version_id: int | None = None,
) -> SyntheticRecord:
    """Build a synthetic Site record matching app/templates/app/website.html expectations."""
    return SyntheticRecord(
        id=site_id,
        slug=slug,
        published_version_id=published_version_id,
    )


def make_version(
    *,
    version_id: int = 1,
    number: int = 1,
    note: str = "Initial service list and contact info",
    created_at: datetime | str | None = None,
    approved_at: datetime | None = None,
    content: dict[str, Any] | None = None,
) -> SyntheticRecord:
    """Build a synthetic SiteVersion record matching app/templates/app/website.html expectations."""
    record = SyntheticRecord(
        id=version_id,
        number=number,
        note=note,
        created_at=created_at if created_at is not None else datetime(2026, 11, 11, 9, 15, 0),
        approved_at=approved_at,
    )
    if content is not None:
        record["content"] = content
    return record


def make_host(
    *,
    hostname: str = "prescott-clean.measurefield.local",
    verified_at: datetime | None = datetime(2026, 11, 10, 12, 0, 0),
) -> SyntheticRecord:
    """Build a synthetic SiteHost record matching app/templates/app/website.html expectations."""
    return SyntheticRecord(hostname=hostname, verified_at=verified_at)


def make_draft(**overrides: Any) -> dict[str, Any]:
    """Build a synthetic draft dictionary matching workspace.py context."""
    base: dict[str, Any] = {
        "name": "Prescott Clean Co.",
        "city": "Prescott, AZ",
        "industry": "cleaning",
        "color": "forest",
        "email": "hello@prescottclean.example",
        "phone": "(928) 555-0142",
        "headline": "Dependable residential cleaning in Prescott",
        "intro": "Clear pricing, careful crews, and punctual arrival windows.",
        "about": "Family-owned cleaning team serving Yavapai County homeowners.",
        "response_window": "next_day",
        "structured_intake": "on",
    }
    base.update(overrides)
    return base


def render_website(
    *,
    site: SyntheticRecord | None = None,
    draft: dict[str, Any] | None = None,
    services_text: str = "Deep cleaning | Top-to-bottom residential clean\nMove-out cleaning",
    versions: list[SyntheticRecord] | None = None,
    hosts: list[SyntheticRecord] | None = None,
    dirty: bool = False,
    permissions: set[str] | None = None,
    csrf: str = "synthetic-csrf-token",
    suggested_slug: str = "prescott-clean",
    base_domain: str = "measurefield.local",
) -> str:
    """Render app/templates/app/website.html using the real Jinja2 environment from app.deps."""
    allowed = permissions if permissions is not None else {"site.edit", "site.publish"}
    template = templates.env.get_template(TEMPLATE_NAME)
    return template.render(
        org=SimpleNamespace(name="Prescott Clean Co.", status="active"),
        brand="MeasuredField",
        role="owner" if "site.publish" in allowed else ("staff" if "site.edit" in allowed else "viewer"),
        path="/app/website",
        principal=SimpleNamespace(
            user=SimpleNamespace(
                is_platform_admin=False,
                name="Synthetic Owner",
                email="owner@example.invalid",
            )
        ),
        orgs=[],
        csrf=csrf,
        flash="",
        flash_error="",
        can=lambda perm: perm in allowed,
        site=site,
        draft=draft if draft is not None else make_draft(),
        services_text=services_text,
        industries=list(DEFAULT_INDUSTRIES),
        colors=list(DEFAULT_COLORS),
        windows=dict(DEFAULT_WINDOWS),
        suggested_slug=suggested_slug,
        base_domain=base_domain,
        versions=versions if versions is not None else [],
        hosts=hosts if hosts is not None else [make_host()],
        dirty=dirty,
    )


def get_website_template_source() -> str:
    """Return raw source text of app/templates/app/website.html from the Jinja2 loader."""
    loader = templates.env.loader
    assert loader is not None, "Expected templates.env.loader to be configured"
    source, _, _ = loader.get_source(templates.env, TEMPLATE_NAME)
    return source


def get_app_css_source() -> str:
    """Return raw source text of app/static/app.css served under style-src 'self'."""
    return APP_CSS_PATH.read_text(encoding="utf-8")


class _WebsiteHTMLInspector(HTMLParser):
    """HTML parser collecting forms, inputs, labels, tables, captions, iframes, and inline attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current_form: dict[str, Any] | None = None
        self.labels: list[dict[str, str | None]] = []
        self._in_label = False
        self._current_label_attrs: dict[str, str | None] = {}
        self._current_label_text: list[str] = []
        self.controls: list[dict[str, str | None]] = []
        self.iframes: list[dict[str, str | None]] = []
        self.tables: int = 0
        self.th_elements: int = 0
        self.captions: list[str] = []
        self._in_caption = False
        self._current_caption_text: list[str] = []
        self.td_data_labels: list[str] = []
        self.inline_style_attrs: list[str] = []
        self.inline_event_attrs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        for attr_name in attr_map:
            lower_attr = attr_name.lower()
            if lower_attr == "style":
                self.inline_style_attrs.append(tag)
            elif lower_attr.startswith("on"):
                self.inline_event_attrs.append(f"{tag}[{lower_attr}]")

        if tag == "form":
            self._current_form = {
                "method": (attr_map.get("method") or "get").lower(),
                "action": attr_map.get("action") or "",
                "inputs": [],
                "buttons": [],
            }
            self.forms.append(self._current_form)
        elif tag in {"input", "select", "textarea"}:
            control_info = {"tag": tag, **attr_map}
            self.controls.append(control_info)
            if self._current_form is not None:
                self._current_form["inputs"].append(control_info)
        elif tag == "button":
            if self._current_form is not None:
                self._current_form["buttons"].append(attr_map)
        elif tag == "label":
            self._in_label = True
            self._current_label_attrs = attr_map
            self._current_label_text = []
        elif tag == "iframe":
            self.iframes.append(attr_map)
        elif tag == "table":
            self.tables += 1
        elif tag == "th":
            self.th_elements += 1
        elif tag == "caption":
            self._in_caption = True
            self._current_caption_text = []
        elif tag == "td" and attr_map.get("data-label") is not None:
            self.td_data_labels.append(attr_map.get("data-label") or "")

    def handle_data(self, data: str) -> None:
        if self._in_label:
            self._current_label_text.append(data)
        if self._in_caption:
            self._current_caption_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current_form = None
        elif tag == "label" and self._in_label:
            self._in_label = False
            self.labels.append(
                {
                    "for": self._current_label_attrs.get("for"),
                    "text": "".join(self._current_label_text).strip(),
                }
            )
        elif tag == "caption" and self._in_caption:
            self._in_caption = False
            self.captions.append("".join(self._current_caption_text).strip())


def _inspect_html(html_text: str) -> _WebsiteHTMLInspector:
    inspector = _WebsiteHTMLInspector()
    inspector.feed(html_text)
    return inspector


def test_draft_vs_live_status_clarity_across_publishing_states() -> None:
    """Draft vs. live published status banner and preview callouts clearly distinguish draft and public states."""
    # Case 1: Unpublished site with unsaved draft changes
    v1_unapproved = make_version(version_id=10, number=1, approved_at=None)
    html_unpublished = render_website(
        site=make_site(published_version_id=None),
        versions=[v1_unapproved],
        dirty=True,
    )
    assert "Offline &middot; Not published" in html_unpublished or "Not published" in html_unpublished
    assert "No version is live to public visitors yet." in html_unpublished
    assert "Has unsaved draft edits not yet saved in a version snapshot." in html_unpublished
    assert "Draft preview only &middot; Not live" in html_unpublished
    assert "not published (offline)" in html_unpublished

    # Case 2: Published site (v1 live) while newer saved version (v2) exists and draft is clean
    v1_live = make_version(
        version_id=10,
        number=1,
        note="Launch version",
        approved_at=datetime(2026, 11, 10, 10, 0, 0),
    )
    v2_saved = make_version(
        version_id=20,
        number=2,
        note="Updated winter services",
        approved_at=datetime(2026, 11, 11, 11, 0, 0),
    )
    html_published_diff = render_website(
        site=make_site(published_version_id=10),
        versions=[v2_saved, v1_live],
        dirty=False,
    )
    assert "Published &amp; live" in html_published_diff
    assert "Version #1 is currently serving public visitors." in html_published_diff
    assert "Saved as version #2 (differs from live version #1)." in html_published_diff
    assert "Published Version #1" in html_published_diff
    assert "which differs from this draft" in html_published_diff
    assert 'href="/app/website/versions/10/view/"' in html_published_diff

    # Case 3: Initial site creation before a site record exists
    html_create = render_website(site=None, suggested_slug="prescott-clean", base_domain="measurefield.local")
    assert "Create your website" in html_create
    assert "Create website draft" in html_create
    assert 'name="slug"' in html_create
    assert "prescott-clean" in html_create
    assert "measurefield.local" in html_create
    assert 'id="draft-preview"' not in html_create


def test_real_form_actions_and_csrf_tokens_preserved() -> None:
    """All workspace website POST routes and CSRF hidden inputs are preserved without inventing endpoints."""
    v1_live = make_version(
        version_id=101,
        number=1,
        approved_at=datetime(2026, 11, 9, 8, 0, 0),
    )
    v2_approved = make_version(
        version_id=102,
        number=2,
        approved_at=datetime(2026, 11, 10, 8, 0, 0),
    )
    v3_unapproved = make_version(
        version_id=103,
        number=3,
        approved_at=None,
    )
    csrf_token = "csrf-secret-987654"
    html = render_website(
        site=make_site(published_version_id=102),
        versions=[v3_unapproved, v2_approved, v1_live],
        csrf=csrf_token,
    )
    inspector = _inspect_html(html)

    post_forms = [f for f in inspector.forms if f["method"] == "post"]
    actions = [f["action"] for f in post_forms]

    expected_actions = [
        "/app/website/save",
        "/app/website/version",
        "/app/website/domains",
        "/app/website/versions/103/approve",
        "/app/website/versions/101/publish",
        "/app/website/unpublish",
    ]
    for expected in expected_actions:
        assert expected in actions, f"Expected POST form action '{expected}' in rendered website editor"

    # Live version #2 (id=102) must not have a redundant publish or approve form
    assert "/app/website/versions/102/publish" not in actions
    assert "/app/website/versions/102/approve" not in actions

    # Every POST form must carry the exact hidden CSRF input
    for form in post_forms:
        csrf_inputs = [
            inp
            for inp in form["inputs"]
            if inp.get("tag") == "input"
            and inp.get("type") == "hidden"
            and inp.get("name") == "csrf"
            and inp.get("value") == csrf_token
        ]
        assert len(csrf_inputs) == 1, f"Form action '{form['action']}' is missing hidden csrf input"


def test_explicit_human_confirmation_for_approve_publish_rollback_and_unpublish() -> None:
    """Approve, publish, rollback, and unpublish require explicit human checkbox confirmation and state no AI auto-publish."""
    v1_old_approved = make_version(
        version_id=11,
        number=1,
        note="Spring layout",
        approved_at=datetime(2026, 11, 1, 9, 0, 0),
    )
    v2_live = make_version(
        version_id=22,
        number=2,
        note="Summer layout",
        approved_at=datetime(2026, 11, 5, 9, 0, 0),
    )
    v3_new_approved = make_version(
        version_id=33,
        number=3,
        note="Autumn layout",
        approved_at=datetime(2026, 11, 10, 9, 0, 0),
    )
    v4_unapproved = make_version(
        version_id=44,
        number=4,
        note="Winter draft snapshot",
        approved_at=None,
    )
    html = render_website(
        site=make_site(published_version_id=22),
        versions=[v4_unapproved, v3_new_approved, v2_live, v1_old_approved],
    )
    inspector = _inspect_html(html)
    forms_by_action = {f["action"]: f for f in inspector.forms if f["method"] == "post"}

    # 1. Approve form for v4 (#4) requires confirm_approve checkbox
    approve_form = forms_by_action["/app/website/versions/44/approve"]
    assert any(
        inp.get("type") == "checkbox"
        and inp.get("name") == "confirm_approve"
        and "required" in inp
        for inp in approve_form["inputs"]
    ), "Approve form must require explicit confirm_approve checkbox"

    # 2. Publish form for newer approved v3 (#3) requires confirm_publish checkbox
    publish_form = forms_by_action["/app/website/versions/33/publish"]
    assert any(
        inp.get("type") == "checkbox"
        and inp.get("name") == "confirm_publish"
        and "required" in inp
        for inp in publish_form["inputs"]
    ), "Publish form must require explicit confirm_publish checkbox"
    assert "Publish version #3 (replace live)" in html
    assert "Confirm live replacement:" in html

    # 3. Rollback form for older approved v1 (#1 < live #2) posts to /publish with rollback messaging and required checkbox
    rollback_form = forms_by_action["/app/website/versions/11/publish"]
    assert any(
        inp.get("type") == "checkbox"
        and inp.get("name") == "confirm_publish"
        and "required" in inp
        for inp in rollback_form["inputs"]
    ), "Rollback form must require explicit confirm_publish checkbox"
    assert "Approved (rollback available)" in html
    assert "Roll back to version #1" in html
    assert "Confirm rollback:" in html
    assert "I confirm rolling back the live website to version #1" in html

    # 4. Unpublish form requires confirm_unpublish checkbox
    unpublish_form = forms_by_action["/app/website/unpublish"]
    assert any(
        inp.get("type") == "checkbox"
        and inp.get("name") == "confirm_unpublish"
        and "required" in inp
        for inp in unpublish_form["inputs"]
    ), "Unpublish form must require explicit confirm_unpublish checkbox"
    assert "Confirm taking website offline:" in html

    # 5. Anti-auto-publish guarantee stated clearly
    assert "Nothing is ever auto-published by AI" in html


@pytest.mark.parametrize(
    ("permissions", "expect_edit", "expect_publish"),
    [
        ({"site.edit", "site.publish"}, True, True),
        ({"site.edit"}, True, False),
        (set(), False, False),
    ],
)
def test_role_permission_gating_for_edit_and_publish_controls(
    permissions: set[str],
    expect_edit: bool,
    expect_publish: bool,
) -> None:
    """Template respects can('site.edit') and can('site.publish') boundaries."""
    v1 = make_version(version_id=10, number=1, approved_at=None)
    html = render_website(
        site=make_site(published_version_id=10),
        versions=[v1],
        permissions=permissions,
    )

    assert ('action="/app/website/save"' in html) is expect_edit
    assert ('action="/app/website/version"' in html) is expect_edit
    if not expect_edit:
        assert "You have read-only access to the website." in html

    assert ('action="/app/website/unpublish"' in html) is expect_publish
    assert ('action="/app/website/domains"' in html) is expect_publish
    assert ('action="/app/website/versions/10/approve"' in html) is expect_publish


def test_no_inline_css_or_js_under_csp_and_autoescaping() -> None:
    """Template contains no inline <style>, style=, <script>, or on* handlers and escapes untrusted values."""
    source = get_website_template_source()
    malicious_draft = make_draft(
        name='<script>alert("xss-name")</script>',
        headline='<img src=x onerror=alert("xss-headline")>',
        about='<b>About</b> & <iframe src="javascript:alert(1)"></iframe>',
    )
    malicious_version = make_version(
        version_id=55,
        number=1,
        note='<svg onload=alert("xss-note")>',
        approved_at=None,
    )
    malicious_host = make_host(hostname='evil.example"<script>alert(1)</script>')

    html = render_website(
        site=make_site(published_version_id=55),
        draft=malicious_draft,
        services_text='<script>alert("xss-services")</script>',
        versions=[malicious_version],
        hosts=[malicious_host],
        dirty=True,
    )
    inspector = _inspect_html(html)
    css = get_app_css_source()

    assert "<style" not in source.lower(), "Inline <style> is forbidden in website.html under CSP"
    assert "<script" not in source.lower(), "Inline <script> is forbidden in website.html under CSP"
    assert re.search(r"\bstyle\s*=", source, flags=re.IGNORECASE) is None
    assert "<style" not in html.lower()
    assert "<script" not in html.lower()
    assert not inspector.inline_style_attrs, f"Unexpected inline style attributes: {inspector.inline_style_attrs}"
    assert not inspector.inline_event_attrs, f"Unexpected inline event attributes: {inspector.inline_event_attrs}"
    assert '/static/app.css' in html
    assert ".triage-summary" in css
    assert ".inquiries-table" in css

    # Verify XSS payloads are escaped
    assert '<script>alert("xss-name")</script>' not in html
    assert '<img src=x onerror=alert("xss-headline")>' not in html
    assert '<svg onload=alert("xss-note")>' not in html
    assert '<script>alert("xss-services")</script>' not in html
    assert "&lt;script&gt;alert(" in html
    assert "&lt;svg onload=" in html


def test_accessible_labels_iframe_title_and_version_table_semantics() -> None:
    """Every form control has an explicit label, preview iframe has a title, and version history uses accessible table markup."""
    v1 = make_version(
        version_id=10,
        number=1,
        note="Initial version",
        approved_at=datetime(2026, 11, 10, 12, 0, 0),
    )
    v2 = make_version(
        version_id=20,
        number=2,
        note="Second version",
        approved_at=None,
    )
    html = render_website(
        site=make_site(published_version_id=10),
        versions=[v2, v1],
        dirty=True,
    )
    inspector = _inspect_html(html)

    label_map = {lbl["for"]: lbl["text"] for lbl in inspector.labels if lbl.get("for")}
    visible_controls = [
        ctrl
        for ctrl in inspector.controls
        if not (ctrl.get("tag") == "input" and ctrl.get("type") == "hidden")
    ]
    assert visible_controls, "Expected visible form controls in rendered website editor"

    for ctrl in visible_controls:
        ctrl_id = ctrl.get("id")
        assert ctrl_id, f"Control {ctrl} is missing an id attribute for label association"
        assert ctrl_id in label_map and label_map[ctrl_id], (
            f"Control id='{ctrl_id}' is missing a non-empty <label for='{ctrl_id}'>"
        )

    # Preview iframe accessibility
    assert len(inspector.iframes) == 1
    iframe_title = inspector.iframes[0].get("title") or ""
    assert "draft" in iframe_title.lower() and "not the live" in iframe_title.lower()
    assert inspector.iframes[0].get("src") == "/app/website/preview/"

    # Version history table semantics and responsive data-label attributes
    assert inspector.tables == 1
    assert inspector.th_elements == 5
    assert inspector.captions and "Saved website versions" in inspector.captions[0]
    assert inspector.td_data_labels == [
        "Version",
        "Saved",
        "Note",
        "Status",
        "Actions",
        "Version",
        "Saved",
        "Note",
        "Status",
        "Actions",
    ]
