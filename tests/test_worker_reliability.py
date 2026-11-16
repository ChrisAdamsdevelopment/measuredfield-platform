"""Synthetic MF-010 worker reliability tests; never send via a real provider."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from .conftest import onboard_business


def _job(kind: str, *, status: str = "queued", attempts: int = 0,
         locked_at=None, payload=None, run_after=None):
    from app.models import Job, now
    return Job(
        kind=kind, idempotency_key="test-mf010:" + uuid.uuid4().hex,
        status=status, attempts=attempts, locked_at=locked_at,
        payload=payload or {}, run_after=run_after or now() - timedelta(days=1000),
        created_at=now(),
    )


def test_retry_backoff_is_bounded_and_exception_payload_is_never_stored(pg_database, monkeypatch):
    from app.db import session_scope
    from app.models import Job, now
    from app.services import jobs

    marker = "DO_NOT_STORE_private-person@example.test_7f9d"
    attempts = []

    def failing(job):
        attempts.append(job.attempts)
        raise RuntimeError("Provider error included customer content " + marker)

    monkeypatch.setitem(jobs.HANDLERS, "mf010_test_failure", failing)
    with session_scope() as db:
        item = _job("mf010_test_failure")
        db.add(item)
        db.flush()
        item_id = item.id

    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        assert jobs.run_due(1) == 1
        with session_scope() as db:
            item = db.get(Job, item_id)
            assert item.attempts == attempt
            assert item.last_error == "RuntimeError"
            if attempt < jobs.MAX_ATTEMPTS:
                assert item.status == "queued"
                assert timedelta(0) < item.run_after - now() <= timedelta(
                    seconds=jobs.MAX_BACKOFF_SECONDS + 2)
                item.run_after = now() - timedelta(days=1000)
            else:
                assert item.status == "failed"
                assert item.finished_at is not None
    assert attempts == list(range(1, jobs.MAX_ATTEMPTS + 1))
    assert marker not in str(jobs.job_status_snapshot(failures_limit=100))


def test_stale_final_attempt_marks_failed_without_running_provider(pg_database, monkeypatch):
    from app.db import session_scope
    from app.models import Job, now
    from app.services import jobs

    called = []
    monkeypatch.setitem(jobs.HANDLERS, "mf010_stale_final", lambda job: called.append(job.id))
    with session_scope() as db:
        item = _job("mf010_stale_final", status="running",
                    attempts=jobs.MAX_ATTEMPTS,
                    locked_at=now() - timedelta(hours=2),
                    payload={"private": "SENSITIVE_STALE_MARKER"})
        db.add(item)
        db.flush()
        item_id = item.id

    jobs.run_due(1)  # The claim pass also repairs expired running leases.
    with session_scope() as db:
        item = db.get(Job, item_id)
        assert item.attempts == jobs.MAX_ATTEMPTS
        assert item.status == "failed"
        assert item.finished_at is not None
        assert item.last_error == "attempt limit reached after worker interruption"
    assert not called


def test_stale_under_limit_reclaimed_after_worker_crash(pg_database, monkeypatch):
    from app.db import session_scope
    from app.models import Job, now
    from app.services import jobs

    seen = []
    monkeypatch.setitem(jobs.HANDLERS, "mf010_stale_recovery", lambda job: seen.append(job.attempts))
    with session_scope() as db:
        item = _job("mf010_stale_recovery", status="running", attempts=1,
                    locked_at=now() - timedelta(hours=3),
                    run_after=now() - timedelta(days=2000))
        db.add(item)
        db.flush()
        item_id = item.id

    assert jobs.run_due(1) == 1
    with session_scope() as db:
        item = db.get(Job, item_id)
        assert item.status == "done"
        assert item.attempts == 2
    assert seen == [2]


def test_lead_notification_replay_reuses_one_message_even_if_base_url_changes(
    platform, monkeypatch,
):
    from app.db import session_scope
    from app.models import EmailMessage, Job, Lead
    from app.services import jobs

    _, org_id, _ = onboard_business(
        platform, business="Synthetic Lead Replay", owner_email="owner@mf010-replay.example",
    )
    with session_scope(org_id=org_id) as db:
        lead = Lead(
            org_id=org_id, name="Synthetic Inquirer",
            email="inquiry@mf010-replay.example", message="Synthetic testing only",
        )
        db.add(lead)
        db.flush()
        lead_id = lead.id

    request = SimpleNamespace(
        org_id=org_id, payload={"lead_id": str(lead_id)}
    )
    jobs._notify_new_lead(request)
    jobs._notify_new_lead(request)

    monkeypatch.setattr(
        jobs, "get_settings",
        lambda: SimpleNamespace(public_base_url="https://renamed-synthetic.example"),
    )
    jobs._notify_new_lead(request)

    with session_scope() as db:
        notices = db.scalars(select(EmailMessage).where(
            EmailMessage.org_id == org_id, EmailMessage.kind == "lead_notice"
        )).all()
        assert len(notices) == 1
        assert "/app/inquiries/" + str(lead_id) in notices[0].body
        assert db.scalars(select(Job).where(
            Job.idempotency_key == f"email:{notices[0].id}"
        )).one().kind == "send_email"


def test_recorded_successful_send_job_replay_does_not_send_twice(pg_database, monkeypatch):
    from app.db import session_scope
    from app.models import EmailMessage
    from app.services import email, jobs

    calls = []

    class SyntheticProvider:
        name = "synthetic"
        def send(self, *, to, subject, body):
            calls.append((to, subject, body))
            return email.SendResult(True, provider_message_id="synthetic-accepted-once")

    monkeypatch.setattr(email, "get_provider", lambda: SyntheticProvider())
    with session_scope() as db:
        message = email.queue_email(
            db, kind="synthetic_notice", to="fictional@example.test",
            subject="Synthetic test", body="Nothing private or real.")
        message_id = message.id

    planned = SimpleNamespace(payload={"message_id": str(message_id)})
    jobs._send_email(planned)
    jobs._send_email(planned)
    with session_scope() as db:
        stored = db.get(EmailMessage, message_id)
        assert stored.status == "sent"
        assert stored.provider_message_id == "synthetic-accepted-once"
    assert len(calls) == 1


def test_job_status_snapshot_contains_no_private_payload_or_error(pg_database):
    from app.db import session_scope
    from app.models import now
    from app.services import jobs

    marker = "PRIVATE_PASSWORD_CANARY_37bb"
    with session_scope() as db:
        item = _job(
            "unrecognized_kind",
            status="failed", attempts=5,
            payload={"password": marker, "recipient": "private@unlisted.example"},
        )
        item.last_error = "Provider included " + marker
        item.finished_at = now()
        db.add(item)
        db.flush()
        item_id = item.id
        idempotency_key = item.idempotency_key

    report = jobs.job_status_snapshot(failures_limit=100)
    assert report["counts"]["failed"] >= 1
    matching = [item for item in report["failed_jobs"] if item["job_id"] == str(item_id)]
    assert len(matching) == 1
    assert matching[0]["kind"] == "unknown"
    assert matching[0]["attempts"] == 5
    rendered = json.dumps(report)
    assert marker not in rendered
    assert "private@unlisted.example" not in rendered
    assert idempotency_key not in rendered
    assert "last_error" not in rendered and "payload" not in rendered


@pytest.mark.parametrize("limit", [0, -1, 101, 1.5, True])
def test_job_status_limit_rejects_invalid_bounds(limit):
    from app.services import jobs
    with pytest.raises(ValueError):
        jobs.job_status_snapshot(failures_limit=limit)


def test_worker_restarts_after_previous_stop_signal(monkeypatch):
    from app import worker
    calls = []

    monkeypatch.setattr(worker, "startup_checks", lambda: None)
    monkeypatch.setattr(worker, "get_settings",
                        lambda: SimpleNamespace(log_level=logging.WARNING))
    monkeypatch.setattr(worker.signal, "signal", lambda *a, **k: None)

    def stop_after_one_batch(batch):
        calls.append(batch)
        worker._STOP.set()
        return 0

    monkeypatch.setattr(worker, "run_due", stop_after_one_batch)
    worker._STOP.set()
    try:
        assert worker.main([]) == 0
        assert worker.main([]) == 0
        assert len(calls) == 2
    finally:
        worker._STOP.clear()


def test_operator_queue_status_cli_is_readonly_and_nonexecuting(monkeypatch, capsys):
    from app import worker

    monkeypatch.setattr(worker, "startup_checks", lambda: None)
    monkeypatch.setattr(worker, "get_settings",
                        lambda: SimpleNamespace(log_level=logging.WARNING))
    monkeypatch.setattr(worker, "run_due", lambda batch: pytest.fail("must not process"))
    monkeypatch.setattr(
        worker, "job_status_snapshot",
        lambda: {"counts": {"failed": 2}, "failed_jobs": [], "due_queued": 0},
    )
    assert worker.main(["--queue-status"]) == 0
    result = json.loads(capsys.readouterr().out.strip())
    assert result["counts"]["failed"] == 2
    assert result["failed_jobs"] == []


@pytest.mark.parametrize("newer_state", ["running", "failed"])
def test_late_worker_cannot_overwrite_newer_attempt_or_terminal_state(
    pg_database, monkeypatch, newer_state,
):
    """A stale handler finishing late must never overwrite subsequent work."""
    from app.db import session_scope
    from app.models import Job, now
    from app.services import jobs

    kind = "mf010_late_" + newer_state
    with session_scope() as db:
        item = _job(kind, run_after=now() - timedelta(days=3000))
        db.add(item)
        db.flush()
        item_id = item.id

    def stale_completion(claimed):
        # Simulate a newer worker successfully claiming the expired lease,
        # or an operator/reaper marking the job permanently failed.
        with session_scope() as db:
            newer = db.get(Job, claimed.id)
            if newer_state == "running":
                newer.attempts = claimed.attempts + 1
                newer.status = "running"
                newer.locked_at = now()
            else:
                newer.status = "failed"
                newer.finished_at = now()
                newer.last_error = "terminalized after replacement claim"

    monkeypatch.setitem(jobs.HANDLERS, kind, stale_completion)
    assert jobs.run_due(1) == 1
    with session_scope() as db:
        row = db.get(Job, item_id)
        assert row.status == newer_state
        assert row.attempts == (2 if newer_state == "running" else 1)
        if newer_state == "failed":
            assert row.last_error == "terminalized after replacement claim"
