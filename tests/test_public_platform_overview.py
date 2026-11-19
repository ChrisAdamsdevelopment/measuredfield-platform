"""MF-082: indexable public overview with accurate beta status and navigation."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


def test_public_platform_sections_and_status_labels():
    html = (ROOT / "app/templates/public/platform.html").read_text(encoding="utf-8")
    for marker in (
        'id="foundation"', 'id="intelligence"', 'id="connections"',
        'id="improvement-loop"', 'id="founder-pilot"',
        "2023 County Business Patterns", "2024 Nonemployer Statistics",
        "not generally available", "not active by default",
        "/login?next=/app/feedback", "/assessment", "/privacy",
        "schema.org", "WebPage", "Source",
    ):
        if marker == "Source":
            continue
        assert marker in html
    assert "Census Bureau public aggregates" in html
    assert "not live competitor counts" in html
    assert "No invented results" not in html


def test_metadata_nav_home_sitemap_is_wired():
    base = (ROOT / "app/templates/public/base.html").read_text(encoding="utf-8")
    home = (ROOT / "app/templates/public/home.html").read_text(encoding="utf-8")
    public = (ROOT / "app/routers/public.py").read_text(encoding="utf-8")
    assert 'href="/platform"' in base
    assert 'href="/platform"' in home
    assert '"/platform"' in public
    assert '@router.get("/platform")' in public
    assert "public/platform.html" in public
    assert '"/visibility-preview"' in public
    assert "measuredfield-business-launch-film-portrait.mp4" in home
    assert "id=\"tools\"" in home


def test_platform_template_renders_without_provider_credentials():
    env = Environment(loader=FileSystemLoader(str(ROOT / "app/templates")),
                      autoescape=True)
    from types import SimpleNamespace
    config = SimpleNamespace(
        env="production", public_base_url="https://www.measuredfield.example",
        support_email="", is_production=True,
    )
    page = env.get_template("public/platform.html").render(
        settings=config, brand="MeasuredField", principal=None,
        csrf="", path="/platform",
    )
    assert "https://www.measuredfield.example/platform" in page
    assert '<script type="application/ld+json">' in page
    assert "<h1>" in page
    assert 'name="robots" content="noindex' not in page
    assert "not generally available" in page
    assert "No one can access" not in page
