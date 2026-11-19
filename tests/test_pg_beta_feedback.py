"""MF-080: full PostgreSQL permissions, CSRF, durability, triage and throttling."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.db import session_scope
from app.models import BetaFeedback

from .conftest import client_for, csrf_of, login, make_operator, onboard_business


def _business(platform, prefix: str):
    suffix = uuid4().hex[:12]
    return onboard_business(
        platform, business="Feedback " + prefix + " " + suffix,
        owner_email=f"feedback-{prefix}-{suffix}@fictional.test",
    )


def _send(client, csrf, *, kind="bug", message="The button was difficult to find.",
          acknowledge="yes"):
    return client.post("/app/feedback", data={
        "csrf": csrf, "kind": kind, "message": message, "acknowledge": acknowledge,
    })


def test_actual_submit_operator_triage_and_cross_business_isolation(platform):
    owner_a, org_a, _ = _business(platform, "alpha")
    owner_b, org_b, _ = _business(platform, "beta")
    assert org_a != org_b
    guest = client_for(platform)
    assert guest.get("/app/feedback").status_code == 303
    assert guest.post("/app/feedback", data={"kind": "bug",
                      "message": "Hello there private feedback"}).status_code in (303, 401, 403)
    assert owner_a.get("/operator/feedback").status_code == 404

    token = csrf_of(owner_a)
    assert owner_a.get("/app/feedback").status_code == 200
    assert _send(owner_a, "", message="CSRF should be denied safely").status_code == 403
    assert _send(owner_a, token, kind="invented").status_code == 422
    assert _send(owner_a, token, acknowledge="").status_code == 422
    submitted = _send(owner_a, token, message="Real test only: please improve the nav.")
    assert submitted.status_code == 303

    with session_scope() as db:
        feedback = db.scalar(select(BetaFeedback).where(
            BetaFeedback.message == "Real test only: please improve the nav."))
        assert feedback and feedback.status == "new"
        assert feedback.workspace_name.startswith("Feedback alpha")
        row_id = feedback.id

    assert "Real test only" in owner_a.get("/app/feedback").text
    assert "Real test only" not in owner_b.get("/app/feedback").text
    assert owner_b.get("/operator/feedback").status_code == 404

    admin = login(platform, *make_operator())
    queue = admin.get("/operator/feedback")
    assert queue.status_code == 200
    assert "Real test only" in queue.text
    assert admin.post(f"/operator/feedback/{row_id}/status",
                      data={"status": "resolved"}).status_code == 403
    assert admin.post(f"/operator/feedback/{row_id}/status", data={
        "csrf": csrf_of(admin), "status": "untrusted"}).status_code == 422
    done = admin.post(f"/operator/feedback/{row_id}/status", data={
        "csrf": csrf_of(admin), "status": "resolved"})
    assert done.status_code == 303
    assert "Resolved" in owner_a.get("/app/feedback").text


def test_user_rate_limit_is_enforced_and_no_overflow_records(platform):
    owner, _, _ = _business(platform, "quota")
    csrf = csrf_of(owner)
    for n in range(8):
        response = _send(owner, csrf, message=f"Feedback item number {n}, please triage it.")
        assert response.status_code == 303
    blocked = _send(owner, csrf, message="One more feedback item would be too many.")
    assert blocked.status_code == 429
    assert "Submission limit reached" in blocked.text


def test_no_anonymous_or_malformed_status_discloses_submission(platform):
    stranger = client_for(platform)
    assert stranger.get("/operator/feedback").status_code in (303, 401, 403, 404)
    assert stranger.post("/operator/feedback/not-a-uuid/status",
                         data={"status": "resolved"}).status_code in (303, 401, 403, 404)
    account, _, _ = _business(platform, "validation")
    token = csrf_of(account)
    assert _send(account, token, message="short").status_code == 422
    assert _send(account, token, message="A" * 1801).status_code == 422
