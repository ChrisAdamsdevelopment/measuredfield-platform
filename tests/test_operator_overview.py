"""Operator dashboard rendering and manual-payment guard regressions."""
from types import SimpleNamespace

from app.config import Settings
from app.deps import templates
from app.routers.operator import order_mark_paid


def render_overview(orders=(), assessments=(), **kwargs):
    ctx = {
        "brand": "MeasuredField", "settings": Settings(), "principal": None,
        "csrf": "test-csrf-only", "flash": "", "flash_error": "",
        "assessments": list(assessments), "orders": list(orders),
        "pending_reviews": 0, "outbox": {}, "jobs": {},
        "pending_domains": [], "email_live": False, "payments_live": False,
    }
    ctx.update(kwargs)
    return templates.env.get_template("operator/overview.html").render(**ctx)


def order(status):
    return SimpleNamespace(
        id="example-order", status=status, offer_code="launch",
        amount_cents=19900, org_id=None, created_at=None, paid_at=None,
        provisioned_at=None,
    )


def assessment(name="Demo Builder"):
    return SimpleNamespace(id="example-assessment", business_name=name,
                           industry="construction", contact_name="Demo",
                           created_at=None, result={})


def test_dashboard_compiles_and_empty_state_renders():
    html = render_overview()
    assert "Operator Console" in html
    assert "No service orders recorded yet" in html
    assert "No assessments submitted yet" in html
    assert 'name="csrf" value="test-csrf-only"' in html


def test_payment_pending_has_mark_paid_but_no_premature_provision_button():
    html = render_overview(orders=[(order("payment_pending"), assessment())])
    assert "Mark paid" in html
    assert "Provision workspace" not in html
    assert "launch" in html
    assert "$199.00" in html


def test_free_consult_can_provision_but_cannot_be_marked_paid():
    html = render_overview(orders=[(order("consult_requested"), assessment())])
    assert "Provision workspace" in html
    assert "Mark paid" not in html


def test_provisioned_order_has_no_mutating_actions_and_escapes_names():
    html = render_overview(orders=[(order("provisioned"), assessment("<script>x</script>"))])
    assert "Provision workspace" not in html
    assert "Mark paid" not in html
    assert "&lt;script&gt;" in html
    assert "<script>x</script>" not in html


def test_manual_payment_requires_actual_reference_before_database_lookup():
    for value in ("", "   ", "abc", "x" * 121):
        response = order_mark_paid("not-a-real-order", reference=value, db=None, p=None)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/operator?mf_notice=")
