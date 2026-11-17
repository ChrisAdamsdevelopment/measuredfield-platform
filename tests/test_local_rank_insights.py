"""MF-049 imported local rank evidence, deterministic recommendations, owner UI."""
from __future__ import annotations

import csv
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.local_rank_insights import LocalRankInputError, analyze_local_rank_csv

HEADER = [
    "city", "keyword", "surface", "latitude", "longitude", "position",
    "competitor", "competitor_position", "checked_at", "provider",
]


def rows_csv(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def row(city="Prescott Valley", keyword="bathroom remodeling", lat="34.61", lon="-112.32",
        position="8", rival="Example Competitor", rival_position="3",
        surface="local_pack", date="2026-11-16T12:00:00-07:00", provider="Partner export"):
    return dict(zip(HEADER, [
        city, keyword, surface, lat, lon, position, rival,
        rival_position, date, provider,
    ]))


def sample():
    entries = []
    for city, start, ranks in [
        ("Prescott Valley", "34.61", (8, 9, 11, 13)),
        ("Prescott", "34.54", (2, 3, 4, 5)),
    ]:
        for offset, pos in enumerate(ranks):
            entries.append(row(city=city, lat=str(float(start) + offset * .005),
                               position=str(pos), rival_position="2"))
    return rows_csv(entries)


def test_two_city_actual_imported_observations_and_actions():
    report = analyze_local_rank_csv(sample())
    assert report["status"] == "owner_imported_preview_not_verified"
    assert report["provenance"] == "owner_imported_not_provider_authenticated"
    assert report["provider_calls_made"] == report["google_queries_made"] == 0
    assert report["unique_samples"] == 8 and report["records_imported"] == 8
    markets = {x["city"]: x for x in report["markets"]}
    assert markets["Prescott Valley"]["average_position"] == 10.2
    assert markets["Prescott"]["average_position"] == 3.5
    assert markets["Prescott Valley"]["top3_share"] == 0
    assert markets["Prescott"]["top3_share"] == .5
    assert markets["Prescott Valley"]["competitors"][0]["ahead_count"] == 4
    titles = " ".join(item["title"] for item in report["recommendations"])
    assert "local relevance in Prescott Valley" in titles
    assert "Example Competitor" in titles
    assert "Compare the Prescott Valley experience with Prescott" in titles
    assert all("reason" in a and "next_step" in a and a["confidence"]
               for a in report["recommendations"])
    assert "not independently verified" in report["limitations"][0]


def test_multiple_competitors_same_point_deduplicates_own_rank():
    a = row()
    b = {**a, "competitor": "Second firm", "competitor_position": "5"}
    report = analyze_local_rank_csv(rows_csv([a, b]))
    assert report["records_imported"] == 2
    assert report["unique_samples"] == 1
    rivals = report["markets"][0]["competitors"]
    assert len(rivals) == 2
    assert report["markets"][0]["ranked_samples"] == 1


def test_missing_rank_never_becomes_not_observed():
    data = rows_csv([row(position="", rival_position="")])
    report = analyze_local_rank_csv(data)
    market = report["markets"][0]
    assert market["average_position"] is None
    assert market["top3_share"] is None
    assert market["unknown_samples"] == 1
    assert market["points"][0]["position"] is None
    assert any("baseline" in r["title"] for r in report["recommendations"])


@pytest.mark.parametrize("update,message", [
    ({"surface": "places"}, "Only organic or local_pack"),
    ({"surface": "ai_answer"}, "Only organic or local_pack"),
    ({"latitude": "NaN"}, "valid geographic"),
    ({"longitude": "-181"}, "valid geographic"),
    ({"position": "101"}, "1 to 100"),
    ({"position": "0"}, "1 to 100"),
    ({"position": "2.5"}, "1 to 100"),
    ({"checked_at": "2026-11-16"}, "timezone"),
    ({"checked_at": "no-time"}, "ISO 8601"),
    ({"competitor": "", "competitor_position": "2"}, "competitor name"),
    ({"keyword": "<script>\n</script>"}, "Invalid keyword"),
])
def test_ambiguous_and_invalid_source_rows_rejected(update, message):
    with pytest.raises(LocalRankInputError, match=message):
        analyze_local_rank_csv(rows_csv([{**row(), **update}]))


def test_conflicting_own_and_competitor_source_rows_rejected():
    base = row()
    with pytest.raises(LocalRankInputError, match="Conflicting positions"):
        analyze_local_rank_csv(rows_csv([base, {**base, "position": "4"}]))
    with pytest.raises(LocalRankInputError, match="Conflicting competitor positions"):
        analyze_local_rank_csv(rows_csv([base, {**base, "competitor_position": "5"}]))


def test_unknown_parameters_and_limits_fail_closed():
    with pytest.raises(LocalRankInputError, match="CSV must include"):
        analyze_local_rank_csv("city,rank\nPrescott,2\n")
    with pytest.raises(LocalRankInputError, match="Maximum 500 rows"):
        analyze_local_rank_csv(rows_csv([row()] * 501))
    with pytest.raises(LocalRankInputError, match="320,000 bytes"):
        analyze_local_rank_csv("x" * 320001)
    with pytest.raises(LocalRankInputError, match="eight cities"):
        analyze_local_rank_csv(rows_csv([row(city=f"City-{n}") for n in range(9)]))
    with pytest.raises(LocalRankInputError, match="Binary data"):
        analyze_local_rank_csv(rows_csv([row()]) + "\x00")


def test_unmatched_city_queries_must_not_report_apples_to_apples():
    result = analyze_local_rank_csv(rows_csv([
        row(city="Prescott", keyword="kitchen remodeling"),
        row(city="Prescott Valley", keyword="bathroom remodeling"),
    ]))
    assert any("apples-to-apples" in a["title"] for a in result["recommendations"])


def test_templates_are_accessible_and_provenance_honest():
    repo = Path(__file__).resolve().parents[1]
    env = Environment(loader=FileSystemLoader(str(repo / "app/templates")),
                      autoescape=select_autoescape(["html"]))
    principal = SimpleNamespace(user=SimpleNamespace(
        name="Synthetic Owner", email="owner@example.invalid", is_platform_admin=False))
    common = dict(org=SimpleNamespace(name="Synthetic Co", status="active"),
                  principal=principal, settings=SimpleNamespace(is_production=False, env="test"),
                  role="owner", orgs=[], brand="MeasuredField",
                  path="/app/modules/local-search", flash="", flash_error="",
                  csrf="synthetic-csrf", request=object())
    html = env.get_template("app/local_visibility.html").render(**common)
    assert 'id="lv-app"' in html and 'data-csrf="synthetic-csrf"' in html
    assert 'id="lv-results"' in html and 'aria-live="polite"' in html
    assert "owner-imported" in html.lower() and "not independently" in html.lower()
    marketing = env.get_template("public/home.html").render(
        brand="MeasuredField",
        settings=SimpleNamespace(is_production=False, env="test",
                                 support_email=None, offers=[]),
        principal=None,
    )
    assert "Local Search Map" in marketing
    assert "Next-Step Intelligence" in marketing
    assert "controlled testing" in marketing
    assert "not a released one-click integration" in marketing


def test_authenticated_local_search_endpoint_csrf_and_no_external_calls(platform):
    from .conftest import client_for, csrf_of, onboard_business

    client, _, _ = onboard_business(
        platform, business="Synthetic Local Studio",
        owner_email="mf049-owner@example.invalid",
    )
    page = client.get("/app/modules/local-search")
    assert page.status_code == 200
    assert "Local Visibility" in page.text or "Local Search" in page.text
    url = "/api/v1/modules/local-search/analyze"
    assert client.post(url, json={"csv_text": sample()}).status_code == 403
    response = client.post(url, json={"csv_text": sample()},
                           headers={"X-CSRF-Token": csrf_of(client)})
    assert response.status_code == 200, response.text[:250]
    assert response.json()["provider_calls_made"] == 0
    assert response.json()["unique_samples"] == 8
    bad = client.post(url, json={"csv_text": rows_csv([row(surface="google_places")])},
                      headers={"X-CSRF-Token": csrf_of(client)})
    assert bad.status_code == 422
    anonymous = client_for(platform)
    assert anonymous.get("/app/modules/local-search").status_code == 303
    assert anonymous.post(url, json={"csv_text": sample()}).status_code in (401, 403)
