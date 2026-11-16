"""MF-026: authenticated dev-only navigation is honest and accessible."""
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]


def render_base(*, production=False, path="/app/lead-rescue"):
    environment = Environment(
        loader=FileSystemLoader(str(ROOT / "app" / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    return environment.get_template("app/base.html").render(
        org=SimpleNamespace(name="Synthetic Test Shop", status="active"),
        role="owner", brand="MeasuredField", orgs=[], csrf="test",
        principal=SimpleNamespace(user=SimpleNamespace(
            name="Fictional Owner", email="owner@example.invalid", is_platform_admin=False)),
        settings=SimpleNamespace(is_production=production),
        path=path, flash=None, flash_error=None,
    )


def test_local_lead_rescue_sidebar_is_discoverable_and_marks_current_page():
    html = render_base()
    assert 'href="/app/lead-rescue" aria-current="page"' in html
    assert "Lead Rescue (local preview)" in html
    assert 'href="/app/modules"' in html
    assert 'href="#main"' in html


def test_live_production_nav_hides_dev_only_lead_rescue_route():
    html = render_base(production=True)
    assert 'href="/app/lead-rescue"' not in html
    assert "Lead Rescue (local preview)" not in html
    assert "Modules (local preview)" not in html
    assert 'href="/app/inquiries"' in html


def test_local_other_page_does_not_falsely_highlight_rescue():
    html = render_base(path="/app/inquiries")
    assert 'href="/app/lead-rescue" aria-current="page"' not in html
    assert 'href="/app/lead-rescue"' in html
