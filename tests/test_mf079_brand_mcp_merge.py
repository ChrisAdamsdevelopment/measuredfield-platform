"""MF-079: keep both live brand/video assets and reviewed MCP/market features."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app/main.py"
BASE = ROOT / "app/templates/public/base.html"
HOME = ROOT / "app/templates/public/home.html"


def test_mcp_and_media_mime_code_survive_merge():
    text = MAIN.read_text(encoding="utf-8")
    assert "import mimetypes" in text and "import os" in text
    assert 'mimetypes.add_type("image/webp"' in text
    assert 'mimetypes.add_type("text/vtt"' in text
    assert 'MF_ENABLE_REMOTE_MCP' in text
    assert "mcp_resource_app" in text
    assert "mcp_host" in text and "_configured_mcp_resource" in text
    assert '"/.well-known/oauth-protected-resource/mcp"' in text


def test_base_preserves_production_brand_and_new_navigation():
    page = BASE.read_text(encoding="utf-8")
    for marker in (
        'href="/#tools"', 'href="/#results"', 'href="/#pricing"',
        'href="/watch/measuredfield-business-launch"',
        "measuredfield-logo-light-transparent.webp",
        "measuredfield-icon-master-transparent.webp",
        "measuredfield-icon-180.png",
        "/static/product_home.css",
    ):
        # product CSS belongs in homepage rather than base
        if marker == "/static/product_home.css":
            continue
        assert marker in page
    assert 'name="robots" content="noindex,nofollow"' in page


def test_home_keeps_updated_product_content_and_production_seo():
    page = HOME.read_text(encoding="utf-8")
    for marker in (
        '{% block title %}', '{% block description %}',
        '{% block social_meta %}', '{% block structured_data %}',
        '/static/product_home.css', 'id="tools"',
        'class="mh-console"', 'id="visual-overview"',
        "measuredfield-hero-business-tools.webp",
        "measuredfield-mobile-hero.webp",
        'class="hero-questions"',
        "measuredfield-business-workspace-99-month.webp",
        "measuredfield-growth-partner-99-month.webp",
        "measuredfield-business-launch-film-portrait.mp4",
        'id="results"',
        "MeasuredField local",  # main+live should retain the local growth example
    ):
        assert marker.lower() in page.lower(), marker
    assert re.search(r'<script type="application/ld\+json">', page)
    assert "NOT current named competitors" not in page
    assert "measuredfield-pbc-traffic-claim-hold" not in page


def test_unique_jinja_blocks_and_no_conflict_markers():
    for file in (MAIN, BASE, HOME):
        text = file.read_text(encoding="utf-8")
        assert "<<<<<<<" not in text
        assert "=======" not in text
        assert ">>>>>>>" not in text
    home = HOME.read_text(encoding="utf-8")
    names = re.findall(r"{%\s*block\s+(\w+)\s*%}", home)
    assert len(names) == len(set(names))


def test_live_media_assets_exist_in_candidate():
    media = ROOT / "app/static/media"
    for name in (
        "measuredfield-hero-business-tools.webp",
        "measuredfield-mobile-hero.webp",
        "measuredfield-social-preview.jpg",
        "measuredfield-logo-dark-transparent.webp",
        "measuredfield-logo-light-transparent.webp",
        "measuredfield-business-launch-film-portrait.mp4",
        "measuredfield-business-launch-film-poster.jpg",
        "measuredfield-business-launch-film.en.vtt",
    ):
        path = media / name
        assert path.is_file(), name
        assert path.stat().st_size > 50, name
