"""MF-090: local acquisition has a truthful, canonical and functional public entry path."""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET

from app.config import get_settings
from .conftest import client_for


def test_local_prescott_page_has_true_offer_and_working_paths(platform):
    client = client_for(platform)
    page = client.get("/local/prescott")
    assert page.status_code == 200
    text = page.text
    base = get_settings().public_base_url.rstrip("/")
    assert f'<link rel="canonical" href="{base}/local/prescott">' in text
    assert '<meta name="description"' in text
    assert "Prescott Valley" in text and "Prescott" in text
    assert "Chino Valley" in text and "Dewey-Humboldt" in text
    assert "Prescott's Best Construction" in text
    assert "https://prescottsbestconstruction.com/" in text
    assert "not a raw performance chart" in text
    assert "not a live scan" in text
    assert 'href="/assessment"' in text
    assert 'href="/signup"' in text
    assert 'href="/visibility-preview"' in text
    assert 'href="/platform"' in text
    assert 'href="/static/prescott_local.css"' in text
    assert "top three Google Maps positions?" in text
    assert "No. Google controls ranking" in text
    assert "not a promise that every MeasuredField tool is fully automated" in text
    assert not any(piece in text for piece in
                   ("We guarantee", "number one in Google", "1,000% ROI", "24/7 onsite", "streetAddress"))
    for target in ("/assessment", "/signup", "/visibility-preview", "/platform"):
        assert client.get(target).status_code == 200, target
    css = client.get("/static/prescott_local.css")
    assert css.status_code == 200
    assert "max-width:640px" in css.text
    assert "local-hero-grid" in css.text


def test_local_prescott_schema_describes_service_area_not_office(platform):
    client = client_for(platform)
    page = client.get("/local/prescott")
    script = re.findall(r'<script type="application/ld[+]json">(.*?)</script>', page.text, re.S)
    assert len(script) == 1
    payload = json.loads(html.unescape(script[0]))
    graph = payload["@graph"]
    web = next(n for n in graph if n["@type"] == "WebPage")
    service = next(n for n in graph if n["@type"] == "Service")
    base = get_settings().public_base_url.rstrip("/")
    assert web["url"] == base + "/local/prescott"
    assert service["provider"]["@id"] == base + "/#organization"
    assert service["areaServed"] == ["Prescott Valley", "Prescott",
                                     "Chino Valley", "Dewey-Humboldt"]
    forbidden = {"streetAddress", "address", "geo", "latitude",
                 "longitude", "aggregateRating", "review", "openingHours"}
    assert not any(k in service for k in forbidden)
    assert not any(k in web for k in forbidden)
    assert not any(n.get("@type") in {"LocalBusiness", "Place"} for n in graph)


def test_prescott_linked_from_home_and_global_navigation(platform):
    client = client_for(platform)
    for path in ("/", "/platform", "/signup"):
        result = client.get(path)
        assert result.status_code == 200
        assert 'href="/local/prescott"' in result.text
    homepage = client.get("/").text
    assert "Prescott-area business growth support." in homepage


def test_sitemap_lists_one_public_canonical_local_url_no_private_routes(platform):
    client = client_for(platform)
    settings = get_settings()
    old_env = settings.env
    try:
        settings.env = "production"
        response = client.get("/sitemap.xml")
        assert response.status_code == 200
        root = ET.fromstring(response.content)
        locs = [el.text for el in root.iter()
                if el.tag.rsplit("}", 1)[-1] == "loc"]
        base = settings.public_base_url.rstrip("/")
        assert locs.count(base + "/local/prescott") == 1
        assert len(locs) == len(set(locs))
        assert len(locs) == 9
        for private in ("/app", "/operator", "/login", "/accept-invite",
                        "/signup/thanks"):
            assert base + private not in locs
    finally:
        settings.env = old_env
