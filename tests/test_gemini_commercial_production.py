"""Checks for the real 2026-11-13 Gemini-generated 30-second launch film and landing media."""
from __future__ import annotations
import html,json,re,struct
from pathlib import Path
import pytest
from .conftest import client_for

def test_homepage_uses_new_brand_pictures_real_proof_and_video(platform):
    c=client_for(platform)
    h=c.get("/")
    assert h.status_code==200
    txt=h.text
    assert 'src="/static/media/measuredfield-logo-light-transparent.webp"' in txt
    assert 'src="/static/media/measuredfield-icon-master-transparent.webp"' in txt
    assert 'srcset="/static/media/measuredfield-mobile-hero.webp"' in txt
    assert 'src="/static/media/measuredfield-how-it-works.webp"' in txt
    assert 'src="/static/media/measuredfield-local-contractor-results-114.webp"' in txt
    assert "+114%" in txt and "Wix" in txt
    assert "point-by-point export" in txt
    assert 'src="/static/media/measuredfield-business-launch-film-portrait.mp4"' in txt
    assert 'poster="/static/media/measuredfield-business-launch-film-poster.jpg"' in txt
    assert 'preload="metadata"' in txt
    assert '/watch/measuredfield-business-launch' in txt
    assert "Measure Build" not in txt  # do not confuse the final Stripe brand

def test_video_watch_metadata_and_pub_date_gate(platform):
    c=client_for(platform)
    page=c.get("/watch/measuredfield-business-launch")
    assert page.status_code==200
    assert "video.other" in page.text
    assert 'name="robots" content="noindex,nofollow"' in page.text
    assert 'property="og:video"' in page.text
    assert "MeasuredField" in page.text
    assert "video/mp4" in page.text
    assert "VideoObject" not in page.text  # no publication date invented for test environment
    from app.config import get_settings
    settings=get_settings()
    prior=settings.video_publication_date
    try:
        settings.video_publication_date="2026-11-13T09:00:00-07:00"
        published=c.get("/watch/measuredfield-business-launch")
        assert published.status_code==200
        snippets=re.findall(r'<script type="application/ld\+json">(.*?)</script>',published.text,re.S)
        assert len(snippets)==1
        schema=json.loads(html.unescape(snippets[0]))
        assert schema["@type"]=="VideoObject"
        assert schema["uploadDate"]=="2026-11-13T09:00:00-07:00"
        assert schema["duration"]=="PT30S"
        assert schema["contentUrl"].endswith("/static/media/measuredfield-business-launch-film-portrait.mp4")
        assert schema["thumbnailUrl"][0].endswith("measuredfield-business-launch-film-poster.jpg")
    finally:
        settings.video_publication_date=prior

def test_video_asset_response_type_and_poster(platform):
    c=client_for(platform)
    for path,typ in [
      ("/static/media/measuredfield-business-launch-film-portrait.mp4","video/mp4"),
      ("/static/media/measuredfield-business-launch-film-poster.jpg","image/jpeg"),
      ("/static/media/measuredfield-commercial-final-frame.jpg","image/jpeg"),
      ("/static/media/measuredfield-logo-dark-transparent.webp","image/webp"),
      ("/static/media/measuredfield-logo-light-transparent.webp","image/webp"),
      ("/static/media/measuredfield-icon-master-transparent.webp","image/webp"),
      ("/static/media/measuredfield-mobile-hero.webp","image/webp"),
      ("/static/media/measuredfield-how-it-works.webp","image/webp"),
      ("/static/media/measuredfield-local-contractor-results-114.webp","image/webp"),
    ]:
        r=c.get(path)
        assert r.status_code==200,(path,r.status_code)
        assert r.headers["content-type"].split(";")[0]==typ,(path,r.headers["content-type"])
        assert len(r.content)>1000,path
    film=(Path(__file__).resolve().parents[1]/"app/static/media/measuredfield-business-launch-film-portrait.mp4")
    assert 8_000_000<=film.stat().st_size<=24_000_000

def test_video_publication_date_validator():
    from app.config import Settings
    assert Settings(_env_file=None,video_publication_date="2026-11-13T08:40:00-07:00").video_publication_date
    with pytest.raises(ValueError):
        Settings(_env_file=None,video_publication_date="2026-11-13")
    with pytest.raises(ValueError):
        Settings(_env_file=None,video_publication_date="yesterday")


def test_accessible_captions_and_transcript(platform):
    c=client_for(platform)
    page=c.get("/watch/measuredfield-business-launch")
    assert page.status_code==200
    assert '<track kind="captions"' in page.text
    assert 'srclang="en"' in page.text
    assert "Read the narration transcript" in page.text
    assert "You've got customers to serve" in page.text
    assert '<track kind="captions"' in c.get("/").text
    cap=c.get("/static/media/measuredfield-business-launch-film.en.vtt")
    assert cap.status_code==200
    assert cap.headers["content-type"].split(";")[0]=="text/vtt"
    assert cap.text.startswith("WEBVTT")
    assert cap.text.count(" --> ")==10
    assert "Build smarter." in cap.text
    assert "MeasuredField." in cap.text

def test_marketing_robot_rules_and_video_sitemap_only_on_real_launch(platform):
    import xml.etree.ElementTree as ET
    from app.config import get_settings
    c=client_for(platform)
    assert "Disallow: /" in c.get("/robots.txt").text
    assert c.get("/sitemap.xml").status_code == 404
    config=get_settings()
    original_env, original_date=config.env,config.video_publication_date
    try:
        config.env="production"
        config.video_publication_date="2026-11-13T15:12:00-07:00"
        robots=c.get("/robots.txt")
        assert robots.status_code==200
        assert "Allow: /" in robots.text
        assert "/sitemap.xml" in robots.text
        sitemap=c.get("/sitemap.xml")
        assert sitemap.status_code==200
        root=ET.fromstring(sitemap.text)
        urls=[u.text for u in root.iter() if u.tag.endswith("}loc")]
        assert any(u.endswith("/watch/measuredfield-business-launch") for u in urls)
        assert not any("/app/" in u or "/operator" in u for u in urls)
        assert "video:video" in sitemap.text
        assert "2026-11-13T15:12:00-07:00" in sitemap.text
    finally:
        config.env,config.video_publication_date=original_env,original_date
