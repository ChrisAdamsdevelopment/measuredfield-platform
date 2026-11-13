"""Production MeasuredField image integration and structured data regression tests."""
from __future__ import annotations
import html,json,re
from pathlib import Path

from .conftest import client_for


def test_homepage_image_assets_and_schema(platform):
    client = client_for(platform)
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert '<link rel="icon"' in body
    assert "/static/media/favicon.ico" in body
    assert "/static/media/measuredfield-icon-180.png" in body
    assert 'name="twitter:card" content="summary_large_image"' in body
    assert 'property="og:image"' in body
    assert 'name="robots" content="noindex,nofollow"' in body  # test/staging not indexable
    assert "measuredfield-logo-light-transparent.webp" in body and "measuredfield-logo-dark-transparent.webp" in body
    assert "measuredfield-hero-business-tools.webp" in body
    assert "measuredfield-business-workspace-99-month.webp" in body
    assert "measuredfield-growth-partner-99-month.webp" in body
    # The chart contains an invented trend and is deliberately NOT published.
    assert "measuredfield-pbc-traffic-claim-hold" not in body
    assert 'loading="lazy"' in body and 'fetchpriority="high"' in body

    matches = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    assert len(matches)==1
    data=json.loads(html.unescape(matches[0]))
    graph=data["@graph"]
    assert data["@context"]=="https://schema.org"
    kinds={x["@type"] for x in graph}
    assert {"Organization","WebSite","Service"} <= kinds
    assert len([x for x in graph if x["@type"]=="Service"]) == 2
    for node in graph:
        assert node["@id"].startswith("http://localhost") or node["@id"].startswith("https://")
    services={x["name"]:x for x in graph if x["@type"]=="Service"}
    assert services["MeasuredField Business Workspace"]["offers"]["price"] == "99.00"
    assert services["MeasuredField Growth Partner"]["offers"]["price"] == "99.00"
    assert services["MeasuredField Growth Partner"]["image"].endswith(
        "/static/media/measuredfield-growth-partner-99-month.webp")


def test_optimized_media_is_served_from_main_app(platform):
    client=client_for(platform)
    for path,mime in [
      ("/static/media/measuredfield-hero-business-tools.webp","image/webp"),
      ("/static/media/measuredfield-logo-primary.webp","image/webp"),
      ("/static/media/measuredfield-brand-icon.webp","image/webp"),
      ("/static/media/measuredfield-business-workspace-99-month.webp","image/webp"),
      ("/static/media/measuredfield-growth-partner-99-month.webp","image/webp"),
      ("/static/media/measuredfield-social-preview.jpg","image/jpeg"),
      ("/static/media/measuredfield-icon-16.png","image/png"),
      ("/static/media/measuredfield-icon-32.png","image/png"),
      ("/static/media/measuredfield-icon-180.png","image/png"),
      ("/static/media/measuredfield-icon-192.png","image/png"),
      ("/static/media/measuredfield-icon-512.png","image/png"),
      ("/static/media/favicon.ico","image/x-icon"),
      ("/static/media/site.webmanifest","application/manifest+json"),
    ]:
        response=client.get(path)
        assert response.status_code==200,path
        assert len(response.content)>80,path
        if path.endswith("webmanifest"):
            manifest=response.json()
            assert manifest["name"]=="MeasuredField"
            assert len(manifest["icons"])>=2
        else:
            assert response.headers["content-type"].split(";")[0]==mime, (path,response.headers["content-type"])


def test_no_private_case_study_asset_in_published_media():
    repo=Path(__file__).resolve().parents[1]
    media=repo/"app"/"static"/"media"
    assert not list(media.glob("*pbc-traffic*"))
    assert not list(media.glob("*contractorresults*"))
