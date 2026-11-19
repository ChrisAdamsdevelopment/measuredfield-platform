"""MF-080: synthetic UI and privacy contracts; no live customer data."""
from __future__ import annotations

from jinja2 import Environment, FileSystemLoader
from pathlib import Path

from app import models
from app.routers import feedback as routes


ROOT = Path(__file__).resolve().parents[1]


def test_support_message_is_deliberately_platform_data_not_tenant_source():
    assert "beta_feedback" not in models.TENANT_TABLES
    assert "org_id" not in models.BetaFeedback.__table__.columns
    assert "submitter_user_id" in models.BetaFeedback.__table__.columns
    assert "workspace_name" in models.BetaFeedback.__table__.columns
    assert routes.MAX_HOURLY_SUBMISSIONS <= 8
    assert set(routes.KINDS) == {"bug", "idea", "other"}


def test_feedback_form_explicit_disclosure_and_accessible_controls():
    template = (ROOT / "app/templates/app/feedback.html").read_text(encoding="utf-8")
    assert "Measur" in template and "support team" in template
    for term in ('name="csrf"', 'name="message"', 'name="kind"',
                 'name="acknowledge"', 'maxlength="1800"', 'role="alert"'):
        assert term in template
    assert "Please do not include passwords" in template


def test_login_gate_and_operator_only_routes_are_declared():
    definitions = [(route.path, route.methods) for route in routes.router.routes]
    assert ("/app/feedback", {"GET"}) in definitions
    assert ("/app/feedback", {"POST"}) in definitions
    assert ("/operator/feedback", {"GET"}) in definitions
    assert ("/operator/feedback/{feedback_id}/status", {"POST"}) in definitions


def test_output_template_autoescapes_feedback_text():
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "app/templates")), autoescape=True,
    )
    t = env.get_template("operator/feedback.html")
    from types import SimpleNamespace
    fake = SimpleNamespace(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", kind="bug", status="new",
        workspace_name="Fictional", message="<script>alert('bad')</script>",
        created_at=__import__("datetime").datetime(2026, 11, 17),
    )
    settings = SimpleNamespace(env="test", is_production=False, support_email="")
    result = t.render(brand="MeasuredField", settings=settings, principal=None,
                      items=[(fake, "fictional@example.invalid")],
                      statuses=("new", "reviewing", "resolved"), csrf="fixture",
                      flash="", flash_error="")
    assert "<script>alert" not in result
    assert "&lt;script&gt;" in result
