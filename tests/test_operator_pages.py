"""Synthetic rendering checks for every authenticated platform operator page."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.config import Settings
from app.deps import templates


def _context(**values):
    ctx = {
        "brand": "MeasuredField", "settings": Settings(), "principal": None,
        "csrf": "test-csrf-token", "flash": "", "flash_error": "",
    }
    ctx.update(values)
    return ctx


def _render(name, **context):
    return templates.env.get_template("operator/" + name + ".html").render(**_context(**context))


def test_all_operator_templates_exist_and_compile():
    for name in ("overview", "assessment", "outbox", "reviews"):
        template = templates.env.get_template("operator/" + name + ".html")
        assert template is not None


def test_assessment_with_empty_orders_and_owner_statements():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    a = SimpleNamespace(
        id="demo-assessment", business_name="Test Plumbing", contact_name="Test Owner",
        email="test@example.invalid", website="https://example.invalid",
        industry="home-services", status="new", created_at=now, consent_at=now,
        excited_text="Expanding repairs", blocker_text="Slow follow-up", result={},
    )
    html = _render("assessment", a=a, r={}, orders=[])
    assert "Test Plumbing" in html
    assert "Expanding repairs" in html
    assert "Slow follow-up" in html
    assert "Back to operator console" in html


def test_assessment_rejects_non_http_website_url():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    a = SimpleNamespace(
        id="demo", business_name="Synthetic", contact_name="Test", email="test@example.invalid",
        website="javascript:alert(1)", industry="other", status="new", created_at=now,
        consent_at=now, excited_text="<script>bad</script>", blocker_text="No admin time", result={},
    )
    html = _render("assessment", a=a, r={}, orders=[])
    assert 'href="javascript:' not in html
    assert "&lt;script&gt;" in html


def test_outbox_statuses_and_untrusted_message_text():
    m = SimpleNamespace(
        id="demo", created_at="2026-11-11", kind="lead_notice",
        to_email="test@example.invalid", subject="New lead", status="not_sent_no_provider",
        provider="none", provider_message_id=None, error=None,
        sent_at=None, body="<script>alert('fake')</script>",
    )
    html = _render("outbox", messages=[m], email_live=False)
    assert "Not configured" in html
    assert "not_sent_no_provider" in html
    assert "&lt;script&gt;" in html
    assert "<script>alert(" not in html


def test_operator_review_pending_actions_require_csrf_and_only_sanitized_body():
    r = SimpleNamespace(id="review-id", title="Synthetic Method", status="pending",
        sanitized_body="Only sanitized example text", checks={"pii":"redacted"},
        created_at="2026-11-11")
    html = _render("reviews", reviews=[r], skills=[])
    assert "Only sanitized example text" in html
    assert 'name="csrf" value="test-csrf-token"' in html
    assert "Approve sanitized lesson" in html
    assert "Reject candidate" in html
    assert "No approved shared skills yet." in html


def test_operator_review_already_decided_not_actionable():
    r = SimpleNamespace(id="review-id", title="Reviewed", status="rejected",
        sanitized_body="Synthetic review", checks={}, created_at="2026-11-11")
    html = _render("reviews", reviews=[r], skills=[])
    assert "A decision has already been recorded" in html
    assert "Approve sanitized lesson" not in html
