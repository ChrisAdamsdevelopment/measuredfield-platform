"""UI asset safety tests: generated art must never replace dynamic account state."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "app" / "static" / "media" / "ui_kit"
ICONS = (
    "dashboard", "workspaces", "visibility", "leads", "communications",
    "trusted_computers", "automations", "insights", "billing",
    "settings", "website", "pages", "performance", "marketing",
    "integrations", "search", "notifications", "account", "filter",
    "help", "share",
)


def test_approved_icon_crops_are_small_transparent_and_correctly_named():
    actual = {p.stem for p in KIT.glob("*.png")}
    assert actual == set(ICONS)
    for stem in ICONS:
        path = KIT / (stem + ".png")
        assert path.stat().st_size < 50_000, stem
        with Image.open(path) as im:
            assert im.size == (128, 128), stem
            assert im.mode == "RGBA", stem
            assert im.getpixel((0, 0))[3] == 0, stem
            assert im.getbbox() is not None, stem


def test_nav_icons_are_decorative_and_dynamic_text_is_not_raster():
    html = (ROOT / "app" / "templates" / "app" / "base.html").read_text("utf-8")
    assert "for href, label, icon_key in links" in html
    assert '/static/media/ui_kit/{{ icon_key }}.png' in html
    assert 'alt=""' in html
    assert '<span>{{ label }}</span>' in html
    for label in ("AI Website Workbench", "Trusted computers", "ChatGPT Connections"):
        assert label in html


def test_connected_accounts_illustration_is_decorative_not_a_fake_link():
    path = KIT / "connected_accounts.webp"
    assert path.is_file()
    assert 0 < path.stat().st_size < 100_000
    with Image.open(path) as im:
        assert im.size == (438, 372)
    text = (ROOT / "app" / "templates" / "app" /
            "mcp_connections.html").read_text("utf-8")
    assert 'src="/static/media/ui_kit/connected_accounts.webp"' in text
    assert 'loading="lazy" alt=""' in text
    assert "These are separate checks" in text
    assert "Not verified" in text
    assert "No account will be connected automatically" in text
