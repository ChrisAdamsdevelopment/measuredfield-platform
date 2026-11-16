"""No-account public preview must be honest, safe, and no-provider-call.

Tests run against the disposable PostgreSQL app with the restricted runtime role.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.models import Assessment, Organization
from .conftest import client_for

URL = "/visibility-preview"


def sample(**updates):
    fields = {
        "business_name": "Canyon Remodelers",
        "city": "Prescott Valley",
        "industry": "construction",
        "website": "https://www.canyon-remodelers.example",
        "sample_acknowledged": "yes",
    }
    fields.update(updates)
    return fields


def test_get_is_public_readable_and_explicitly_sample_only(platform):
    response = client_for(platform).get(URL)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "no one connects to your computer" in body.lower()
    assert "not a website audit" in body.lower()
    assert 'method="post" action="/visibility-preview"' in body
    assert 'name="sample_acknowledged"' in body
    assert "before" in body.lower() and "after" in body.lower()
    assert "site.css" in body
    assert 'name="business_name" maxlength="90"' in body
    assert 'name="city" maxlength="100"' in body
    assert "example-results" not in body


def test_valid_submission_generates_illustrative_before_after_not_rank(platform):
    response = client_for(platform).post(URL, data=sample())
    assert response.status_code == 200, response.text[:350]
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "Canyon Remodelers" in body
    assert "Prescott Valley" in body
    assert "Make more of the place you call home" in body
    assert "Quality work. Great service." in body
    assert "kitchen remodeling Prescott Valley" in body
    assert "canyon-remodelers.example" in body
    assert "Not measured" in body
    assert "Not accessed or scanned" in body
    assert "existing MeasuredField copy templates only" in body
    assert "not an image" in body.lower()
    assert "Nothing was published" in body


def test_no_owner_claim_and_no_website_field_needed(platform):
    response = client_for(platform).post(
        URL, data=sample(website="", business_name="Downtown Florist")
    )
    assert response.status_code == 200
    assert "Not provided" in response.text
    assert "Downtown Florist" in response.text


def test_form_must_acknowledge_that_result_is_only_a_sample(platform):
    response = client_for(platform).post(
        URL, data=sample(sample_acknowledged="")
    )
    assert response.status_code == 422
    assert "Confirm that you understand" in response.text
    assert 'aria-invalid="true"' in response.text
    assert "example-results" not in response.text


@pytest.mark.parametrize("field,value,expected", [
    ("website", "http://canyon.example", "public HTTPS website"),
    ("website", "http://127.0.0.1", "public HTTPS website"),
    ("website", "https://localhost", "public HTTPS website"),
    ("website", "file:///C:/private/data", "public HTTPS website"),
    ("website", "https://canyon.example/private-area", "public HTTPS website"),
    ("website", "https://canyon.example:8443", "public HTTPS website"),
    ("website", "https://[::1]", "public HTTPS website"),
    ("website", "https://0x7f000001", "public HTTPS website"),
    ("website", "https://staff:password@canyon.example", "public HTTPS"),
    ("website", "A" * 251, "under 250"),
    ("city", " ", "Enter a city"),
    ("city", "C" * 101, "2 to 100 characters"),
    ("industry", "not-a-real-industry", "supported type"),
    ("business_name", "X", "Enter a business name"),
    ("business_name", "B" * 91, "2 to 90 characters"),
])
def test_invalid_or_private_data_not_accepted(platform, field, value, expected):
    response = client_for(platform).post(URL, data=sample(**{field: value}))
    assert response.status_code == 422
    assert expected in response.text
    assert "example-results" not in response.text


def test_html_is_escaped_not_script_or_markup(platform):
    response = client_for(platform).post(
        URL, data=sample(
            business_name='<script>alert("x")</script> Business',
            city='<img src=x onerror=alert(1)>',
            website="",
        ),
    )
    assert response.status_code == 200
    assert "<script>alert" not in response.text
    assert "<img src=x" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;img" in response.text


def test_honeypot_gives_no_result_and_no_lead(platform):
    response = client_for(platform).post(
        URL, data=sample(company_site="click-this")
    )
    assert response.status_code == 303
    assert response.headers["location"] == URL


def test_rate_limit_is_persisted_across_new_clients_and_denies_ninth(platform):
    for attempt in range(8):
        response = client_for(platform).post(
            URL, data=sample(business_name=f"Canyon Shop {attempt}")
        )
        assert response.status_code == 200, (attempt, response.text[:350])
    denied = client_for(platform).post(URL, data=sample())
    assert denied.status_code == 429
    assert "Too many sample requests" in denied.text
    assert "example-results" not in denied.text


def test_no_assessment_or_tenant_created_from_public_sample(platform):
    with session_scope() as db:
        before_org = db.scalar(select(func.count()).select_from(Organization))
        before_assess = db.scalar(select(func.count()).select_from(Assessment))
    response = client_for(platform).post(URL, data=sample())
    assert response.status_code == 200
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == before_org
        assert db.scalar(select(func.count()).select_from(Assessment)) == before_assess


def test_source_route_does_not_invoke_provider_or_local_machine():
    import inspect
    from app.routers import public
    implementation = inspect.getsource(public.visibility_preview_sample)
    assert "requests.get(" not in implementation
    assert "httpx.get(" not in implementation
    assert "subprocess" not in implementation
    assert "mcp" not in implementation
    assert "sitegen.render" not in implementation


def test_honeypot_attempts_consume_public_rate_limit(platform):
    for _ in range(8):
        response = client_for(platform).post(URL, data=sample(company_site="robot"))
        assert response.status_code == 303
    ninth = client_for(platform).post(URL, data=sample(company_site="robot"))
    assert ninth.status_code == 429
    assert "Too many sample requests" in ninth.text


def test_website_homepage_domain_is_normalized_and_not_visited(platform):
    response = client_for(platform).post(
        URL, data=sample(website="https://WWW.CANYON.EXAMPLE/")
    )
    assert response.status_code == 200
    assert "<dd>canyon.example</dd>" in response.text
    assert "Not accessed or scanned" in response.text
