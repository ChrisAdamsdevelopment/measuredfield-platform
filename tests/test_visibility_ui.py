"""MF-018: deterministic UI template, CSS and JS contract tests for AI Visibility Lab."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.module_catalog import catalog, connection_policy
from app.services.visibility_lab import BENCHMARK_22, MARKETS, MODES


REPO = Path(__file__).resolve().parents[1]


def _render_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(REPO / "app" / "templates")),
        autoescape=select_autoescape(["html"]),
    )


def _base_context(path: str = "/app/modules/visibility") -> dict:
    user = SimpleNamespace(name="Demo Owner", email="demo@example.invalid", is_platform_admin=False)
    principal = SimpleNamespace(user=user)
    return dict(
        org=SimpleNamespace(name="Example Remodeling LLC", status="active"),
        principal=principal,
        settings=SimpleNamespace(is_production=False),
        role="owner",
        orgs=[],
        brand="MeasuredField",
        path=path,
        flash="",
        flash_error="",
        csrf="synthetic-csrf-token",
        request=object(),
    )


def _benchmark_items() -> list[dict]:
    return [{"id": f"q-seed-{i}", "text": q} for i, q in enumerate(BENCHMARK_22, 1)]


def test_modules_template_renders_full_capabilities_and_security_contract():
    env = _render_env()
    html = env.get_template("app/modules.html").render(
        **_base_context("/app/modules"),
        modules=catalog(),
        connection=connection_policy(),
    )
    assert "AI Visibility Lab" in html
    assert "Computer Connect" in html
    assert "Evidence provenance" in html
    assert "Tracked opportunities" in html
    assert 'Remote access enabled: <strong>No</strong>' in html
    assert 'Default permissions: <strong>Deny</strong>' in html
    assert "Zero fake results" in html


def test_visibility_lab_template_has_multi_market_checkboxes_and_all_three_methods():
    env = _render_env()
    html = env.get_template("app/visibility_lab.html").render(
        **_base_context("/app/modules/visibility"),
        questions=_benchmark_items(),
        markets=list(MARKETS),
        modes=list(MODES),
    )
    # All 22 benchmark questions rendered as checkboxes
    assert html.count('class="ml-benchmark"') == 22
    for q_text in BENCHMARK_22:
        assert q_text in html

    # Multiple market checkboxes instead of a single-select dropdown
    assert '<select id="ml-market">' not in html
    assert html.count('class="ml-market"') == len(MARKETS)
    for market in MARKETS:
        assert f'value="{market}"' in html

    # All three search methods present
    assert html.count('class="ml-mode"') == 3
    for mode in MODES:
        assert f'value="{mode}"' in html

    # Required explicit selection & workflow controls
    assert 'id="ml-all"' in html
    assert 'id="ml-approved-only"' in html
    assert 'id="ml-none"' in html
    assert 'id="ml-gsc-results"' in html
    assert 'id="ml-plan"' in html
    assert 'id="ml-export"' in html
    assert 'id="ml-import-file"' in html
    assert 'id="ml-export-draft"' in html
    assert "0 provider calls executed" in html
    assert "No automatic localStorage" in html


def test_visibility_lab_js_enforces_explicit_approval_and_no_localstorage():
    js_path = REPO / "app" / "static" / "module_lab.js"
    js = js_path.read_text(encoding="utf-8")

    # Never use automatic browser localStorage or sessionStorage for sensitive owner data
    assert "localStorage.setItem" not in js
    assert "sessionStorage.setItem" not in js

    # Explicit approval & multi-market support
    assert "approved: false" in js
    assert "ml-approved-only" in js
    assert 'input[name="ml-market"]' in js
    assert 'input[name="ml-mode"]' in js
    assert "source_queries" in js
    assert "restoreDraftFromJson" in js
    assert "imported_from_json: true" in js
    assert "Imported JSON flag (unverified)" in js
    assert "imported_draft_unverified" in js


def test_visibility_lab_css_has_mobile_390px_and_desktop_responsive_rules():
    css_path = REPO / "app" / "static" / "module_lab.css"
    css = css_path.read_text(encoding="utf-8")
    assert "@media(max-width:900px)" in css
    assert "@media(max-width:570px)" in css
    assert ".ml-lineage-chip" in css
    assert ".ml-evidence-grid" in css
    assert ".ml-formula-bar" in css
