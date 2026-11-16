"""MF-009 owner-controlled AI recommendation review. No paid provider or site mutation."""
from __future__ import annotations

import uuid
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.deps import templates
from app.services.lead_metrics import summarize_responses
from app.services.native_growth import review_from_counts
from .conftest import csrf_of, onboard_business


class Forms(HTMLParser):
    """Extract form actions/fields without a browser dependency."""
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action"), "inputs": [], "buttons": []}
            self.forms.append(self.current)
        elif self.current is not None:
            if tag == "input":
                self.current["inputs"].append(attrs)
            elif tag == "button":
                self.current["buttons"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def _context(*, can_write=True):
    return {
        "brand": "MeasuredField",
        "settings": SimpleNamespace(is_production=False),
        "principal": SimpleNamespace(user=SimpleNamespace(
            name="Synthetic Owner", email="owner@example.invalid",
            is_platform_admin=False,
        )),
        "org": SimpleNamespace(name="Synthetic Company", status="active"),
        "orgs": [], "role": "owner", "csrf": "synthetic-csrf",
        "can": lambda permission: can_write if permission == "improvements.write" else True,
        "flash": "", "flash_error": "",
        "statuses": ("proposed", "approved", "implemented", "dismissed"),
        "site": None,
        "measured": {"page_view": 42, "form_start": 2, "lead": 0},
    }


def _item(status="proposed", evidence_status="owner_stated", source="assessment"):
    return SimpleNamespace(
        id=uuid.uuid4(), status=status, title="Reply faster to inquiries",
        detail="Review call-back workflow before changing anything.",
        source=source, capability="inquiry_desk",
        evidence_status=evidence_status, evidence_note="Owner described the problem.",
        metric="First response status within 24 hours",
        implemented_at=None,
    )


def improvements(*, status="proposed", evidence_status="owner_stated",
                 source="assessment", can_write=True, title=None):
    ctx = _context(can_write=can_write)
    item = _item(status, evidence_status, source)
    if title is not None:
        item.title = title
    ctx.update(path="/app/improvements", report=None, items=[item])
    html = templates.env.get_template("app/improvements.html").render(**ctx)
    return html, item


def insights(*, report, can_write=True):
    from datetime import datetime, timezone
    ctx = _context(can_write=can_write)
    ctx.update(
        path="/app/insights", report=report,
        leads_responded=0, leads_total=2,
        response_metrics=summarize_responses([], as_of=datetime(
            2026, 11, 16, tzinfo=timezone.utc)),
        node=False,
    )
    return templates.env.get_template("app/insights.html").render(**ctx)


def test_improvements_show_observed_inferred_and_unknown_with_limits():
    rendered, _ = improvements()
    assert "Observed" in rendered
    assert "Inferred" in rendered
    assert "Unknown" in rendered
    assert "You told us" in rendered
    assert "not an independently observed cause" in rendered
    assert "Implementation time, fees and expected benefit" in rendered
    assert "Approving here never sends messages" in rendered
    assert "accesses files" in rendered
    assert "measured results" not in rendered.lower()


@pytest.mark.parametrize(
    "status,allowed,disallowed,description", [
        ("proposed", {"approved", "dismissed"}, {"implemented"}, "Awaiting a human decision"),
        ("approved", {"implemented", "dismissed"}, {"approved"}, "Nothing has been executed"),
        ("implemented", {"proposed"}, {"approved"}, "verify the result separately"),
        ("dismissed", {"proposed"}, {"approved"}, "no follow-up is scheduled"),
    ],
)
def test_improvements_review_controls_are_csrf_scoped_to_current_status(
    status, allowed, disallowed, description,
):
    rendered, item = improvements(status=status)
    assert description.lower() in rendered.lower()
    forms = Forms()
    forms.feed(rendered)
    relevant = [f for f in forms.forms
                if f["action"] == f"/app/improvements/{item.id}/status"]
    assert len(relevant) == 1
    form = relevant[0]
    assert any(i.get("name") == "csrf" and i.get("value") == "synthetic-csrf"
               for i in form["inputs"])
    values = {button.get("value") for button in form["buttons"]
              if button.get("name") == "status"}
    assert values == allowed
    assert not values.intersection(disallowed)
    assert all(button.get("type") == "submit" for button in form["buttons"])
    assert "Review this improvement" in rendered
    assert f'decision-help-{item.id}' in rendered


def test_readonly_member_has_no_approve_or_dismiss_forms():
    rendered, item = improvements(can_write=False)
    assert "Read-only access" in rendered
    assert f"/app/improvements/{item.id}/status" not in rendered
    assert "Approve plan only" not in rendered
    assert "Dismiss proposal" not in rendered


def test_evidence_categories_are_not_upgraded_to_measured_by_display():
    for code, expected in [
        ("measured", "Observed &middot; measured"),
        ("system_recorded", "Observed &middot; platform record"),
        ("owner_stated", "You told us &middot; not verified"),
        ("unknown", "Unknown &middot; no evidence yet"),
    ]:
        rendered, _ = improvements(evidence_status=code)
        assert expected in rendered


def test_insights_renders_inferred_recommendations_with_unknown_costs():
    report = review_from_counts("Synthetic Studio", {
        "page_view": 64, "form_start": 1, "lead": 0})
    assert report["analysis"]["actions"]
    rendered = insights(report=report)
    assert "Inferred &middot; experiment proposal" in rendered
    assert "Observed" in rendered
    assert "Unknown" in rendered
    assert "Implementation time, labor charges, provider pricing" in rendered
    assert "token estimates" in rendered
    assert "No paid AI model is invoked" in rendered
    assert "Proposed &middot; not approved" in rendered
    assert "Save as proposed improvement" in rendered
    assert 'action="/app/improvements"' in rendered
    assert 'name="csrf" value="synthetic-csrf"' in rendered
    assert "not approved" in rendered
    assert "no email" in rendered.lower()
    assert "Open the improvement review list" in rendered


def test_insights_import_button_only_saves_proposal_data_and_escapes_html():
    malicious = '<img src=x onerror="alert(1)">'
    report = {"analysis": {
        "actions": [{
            "claim": malicious, "experiment": f"Review {malicious}",
            "metric": "Count review outcomes", "priority": "medium",
            "approvalRequired": True, "status": "proposed",
            "evidence": ["analytics.totals.pageViews"],
        }],
        "methodology": "Rules from fictional counters."
    }, "unknowns": ["Revenue is not available."]}
    rendered = insights(report=report)
    assert "<img src=x" not in rendered
    assert "&lt;img" in rendered
    assert "Review" in rendered
    forms = Forms()
    forms.feed(rendered)
    relevant = [f for f in forms.forms if f["action"] == "/app/improvements"]
    assert len(relevant) == 1
    form = relevant[0]
    fields = {i.get("name"): i.get("value") for i in form["inputs"]}
    assert fields["title"] == malicious
    assert "Suggested experiment for owner review" in fields["detail"]
    assert "not yet verified or authorized" in fields["detail"]
    assert fields["metric"] == "Count review outcomes"
    assert fields["csrf"] == "synthetic-csrf"
    assert "status" not in fields
    assert not any(b.get("name") == "status" for b in form["buttons"])


def test_insights_readonly_and_unavailable_engine_have_no_action_buttons():
    report = review_from_counts("Fictional", {"page_view": 0, "form_start": 0, "lead": 0})
    readonly = insights(report=report, can_write=False)
    assert "only an authorized workspace member can save" in readonly
    assert 'action="/app/improvements"' not in readonly
    unavailable = insights(report=None)
    assert "The review engine is unavailable" in unavailable
    assert "No recommendation or outcome is inferred" in unavailable


def test_improvement_and_insight_text_is_escaped_not_executable():
    malicious = '<svg onload="alert(1)"></svg>'
    rendered, _ = improvements(title=malicious)
    assert malicious not in rendered
    assert "&lt;svg" in rendered
    assert "onload" in rendered  # inert escaped text, not a live element
    assert '<script' not in rendered
    assert "onsubmit=" not in rendered
    assert "onclick=" not in rendered


def test_saving_and_approving_suggestion_only_changes_improvement_state(platform):
    from app.db import session_scope
    from app.models import EmailMessage, Improvement, Site
    nonce = uuid.uuid4().hex[:10]
    owner, org_id, _ = onboard_business(
        platform, business=f"Owner Approval {nonce}",
        owner_email=f"owner-{nonce}@mf009.example",
    )
    with session_scope(org_id=org_id) as db:
        baseline = {
            "emails": db.scalar(select(func.count()).select_from(EmailMessage).where(
                EmailMessage.org_id == org_id)),
            "site_versions": db.scalar(select(func.count()).select_from(Site).where(
                Site.org_id == org_id, Site.published_version_id.is_not(None))),
        }
    csrf = csrf_of(owner)
    create = owner.post("/app/improvements", data={
        "csrf": csrf, "title": "Fictional proposal only",
        "detail": "Suggested experiment for owner review; not yet verified or authorized.",
        "metric": "Track inquiry counts from consented events.",
    })
    assert create.status_code == 303
    with session_scope(org_id=org_id) as db:
        row = db.scalar(select(Improvement).where(
            Improvement.org_id == org_id, Improvement.title == "Fictional proposal only",
        ))
        assert row is not None
        item_id = row.id
        assert row.status == "proposed"
        assert row.evidence_status == "unknown"
    approve = owner.post(f"/app/improvements/{item_id}/status", data={
        "csrf": csrf, "status": "approved",
        "evidence_note": "Owner reviewed this plan; no work has begun.",
    })
    assert approve.status_code == 303
    with session_scope(org_id=org_id) as db:
        row = db.get(Improvement, item_id)
        assert row.status == "approved"
        assert row.implemented_at is None
        assert db.scalar(select(func.count()).select_from(EmailMessage).where(
            EmailMessage.org_id == org_id)) == baseline["emails"]
        assert db.scalar(select(func.count()).select_from(Site).where(
            Site.org_id == org_id, Site.published_version_id.is_not(None))) == baseline["site_versions"]
    decline = owner.post(f"/app/improvements/{item_id}/status", data={
        "csrf": csrf, "status": "dismissed",
    })
    assert decline.status_code == 303
    with session_scope(org_id=org_id) as db:
        assert db.get(Improvement, item_id).status == "dismissed"



def test_insights_empty_suggestions_and_unknowns_are_explicit():
    """Missing recommendation rows must not invent an experiment or verified result."""
    report = {
        "analysis": {"actions": [], "methodology": "Deterministic synthetic rules."},
        "unknowns": [],
    }
    rendered = insights(report=report)
    assert "No actionable suggestions were generated" in rendered
    assert "No additional unknowns were supplied" in rendered
    assert "costs and actual outcomes still need independent evidence" in rendered
    assert "Save as proposed improvement" not in rendered
    assert 'action="/app/improvements"' not in rendered
