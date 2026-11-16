"""MF-005 platform-operator audit tests: synthetic actions, real PG and migration."""
from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from .conftest import (
    ADMIN_URL, ROOT, client_for, csrf_of, login, make_operator,
    onboard_business,
)


def _operator(platform):
    email, password = make_operator()
    client = login(platform, email, password)
    csrf = csrf_of(client)
    from app.db import session_scope
    from app.models import User
    with session_scope() as db:
        actor = db.scalar(select(User).where(User.email == email)).id
    return client, csrf, actor


def test_operator_success_and_denial_are_audited_without_payment_details(platform):
    from app.db import session_scope
    from app.models import PlatformSecurityEvent, ServiceOrder
    from .test_pg_auth_payments import _start_paid_order

    unique = uuid.uuid4().hex[:12]
    visitor, token, submitted, order_id, _ = _start_paid_order(
        platform, f"audited-{unique}@mf005.example",
        f"Audit Sample {unique}",
    )
    assert submitted.status_code == 303
    operator, csrf, actor = _operator(platform)
    invalid = operator.post(
        f"/operator/orders/{order_id}/mark-paid",
        data={"csrf": csrf, "reference": " "}
    )
    assert invalid.status_code == 303
    with session_scope() as db:
        events = db.scalars(select(PlatformSecurityEvent).where(
            PlatformSecurityEvent.actor_user_id == actor,
            PlatformSecurityEvent.target_id == order_id,
            PlatformSecurityEvent.action == "order.mark_paid",
        ).order_by(PlatformSecurityEvent.created_at)).all()
        assert len(events) == 1
        assert events[0].outcome == "denied"
        assert events[0].reason_code == "invalid_reference"
        assert db.get(ServiceOrder, order_id).paid_at is None

    reference = "Synthetic Receipt 71298"
    accepted = operator.post(
        f"/operator/orders/{order_id}/mark-paid",
        data={"csrf": csrf, "reference": reference},
    )
    assert accepted.status_code == 303
    repeated = operator.post(
        f"/operator/orders/{order_id}/mark-paid",
        data={"csrf": csrf, "reference": "Different receipt 98765"},
    )
    assert repeated.status_code == 303
    with session_scope() as db:
        events = db.scalars(select(PlatformSecurityEvent).where(
            PlatformSecurityEvent.actor_user_id == actor,
            PlatformSecurityEvent.target_id == order_id,
            PlatformSecurityEvent.action == "order.mark_paid",
        )).all()
        assert sorted(e.outcome for e in events) == [
            "approved", "denied", "denied",
        ]
        assert {e.reason_code for e in events} == {
            "", "invalid_reference", "already_paid",
        }
        paid = db.get(ServiceOrder, order_id)
        assert paid.paid_at is not None
        assert reference in paid.payment_evidence
        recorded = str([(e.action, e.outcome, e.reason_code) for e in events])
        assert reference not in recorded
        assert "mf005.example" not in recorded


def test_operator_only_security_feed_has_no_secrets_and_cannot_be_cached(platform):
    from app.db import session_scope
    from app.models import PlatformSecurityEvent
    from app.services.platform_audit import record_operator_action

    operator, csrf, actor = _operator(platform)
    with session_scope() as db:
        record_operator_action(
            db, actor_user_id=actor, action="jobs.run", outcome="approved",
            target_type="job_queue",
        )
    response = operator.get("/operator/security-events")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    events = response.json()["events"]
    assert any(e["action"] == "jobs.run" and e["actor_user_id"] == str(actor)
               for e in events)
    assert response.json()["limit"] == 50
    for forbidden in ("password", "email", "token", "metadata", "payment_evidence",
                      "payload", "csrf"):
        assert forbidden not in response.text.lower()

    assert client_for(platform).get("/operator/security-events").status_code in (302, 303, 401)
    unique = uuid.uuid4().hex[:9]
    owner, _, _ = onboard_business(
        platform, business=f"Ordinary Owner {unique}",
        owner_email=f"owner-{unique}@mf005.example",
    )
    assert owner.get("/operator/security-events").status_code == 404
    assert owner.post(
        "/operator/jobs/run", data={"csrf": csrf_of(owner)}
    ).status_code == 404


def test_audit_history_is_database_immutable(pg_database):
    from app.db import session_scope
    from app.models import PlatformSecurityEvent
    from app.services.platform_audit import record_operator_action

    actor = uuid.uuid4()
    with session_scope() as db:
        event = record_operator_action(
            db, actor_user_id=actor, action="skill.rollback",
            outcome="denied", target_type="shared_skill",
            reason_code="no_active_version",
        )
        event_id = event.id

    with pytest.raises(DBAPIError, match="immutable"):
        with session_scope() as db:
            db.execute(text(
                "UPDATE platform_security_events SET outcome='approved' WHERE id=:id"
            ), {"id": event_id})
    with pytest.raises(DBAPIError, match="immutable"):
        with session_scope() as db:
            db.execute(text(
                "DELETE FROM platform_security_events WHERE id=:id"
            ), {"id": event_id})
    with session_scope() as db:
        event = db.get(PlatformSecurityEvent, event_id)
        assert event is not None
        assert (event.action, event.outcome, event.reason_code) == (
            "skill.rollback", "denied", "no_active_version",
        )


def test_denied_response_persists_across_request_rollback(platform):
    from app.db import session_scope
    from app.models import PlatformSecurityEvent
    operator, csrf, actor = _operator(platform)
    missing = uuid.uuid4()
    response = operator.post(
        f"/operator/orders/{missing}/provision",
        data={"csrf": csrf, "reason": "Test only"},
    )
    assert response.status_code == 404
    with session_scope() as db:
        event = db.scalar(select(PlatformSecurityEvent).where(
            PlatformSecurityEvent.actor_user_id == actor,
            PlatformSecurityEvent.action == "order.provision",
            PlatformSecurityEvent.target_id == missing,
        ))
        assert event is not None
        assert (event.outcome, event.reason_code) == ("denied", "not_found")


def test_allowed_action_has_only_enum_metadata():
    from app.services.platform_audit import record_operator_action
    kwargs = {
        "db": None, "actor_user_id": uuid.uuid4(), "action": "jobs.run",
        "outcome": "denied", "target_type": "job_queue",
        "reason_code": "not_found",
    }
    with pytest.raises(ValueError):
        record_operator_action(**{**kwargs, "action": "password_reset_token_abcdef"})
    with pytest.raises(ValueError):
        record_operator_action(**{**kwargs, "target_type": "customer-email@example"})
    with pytest.raises(ValueError):
        record_operator_action(**{**kwargs, "reason_code": "Token=private-secret"})
    with pytest.raises(ValueError):
        record_operator_action(**{**kwargs, "target_id": "secret:abc"})
    with pytest.raises(ValueError):
        record_operator_action(**{**kwargs, "outcome": "approved"})


def test_populated_postgres_upgrade_retains_existing_operator_accounts(pg_database):
    """Migrate an independent populated PG database from 0006 to 0007.

    The production-structure fixture's own DB is left untouched. This uses
    the same localhost-only test admin credentials, owner role and Alembic
    revision chain, dropping the separate database in finally.
    """
    from alembic import command
    from alembic.config import Config
    from app.config import get_settings
    from .conftest import _sa

    original = os.environ.get("DATABASE_URL", "")
    base = make_url(pg_database["owner_url"])
    suffix = secrets.token_hex(4)
    name = "mf_audit_populated_" + suffix
    username = base.username
    assert username and username.replace("_", "").isalnum()
    assert name.replace("_", "").isalnum()
    admin = create_engine(_sa(ADMIN_URL), isolation_level="AUTOCOMMIT")
    owner_url = base.set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    seed_id = uuid.uuid4()
    try:
        with admin.connect() as c:
            c.execute(text(f"CREATE DATABASE {name} OWNER {username}"))
        os.environ["DATABASE_URL"] = owner_url
        get_settings.cache_clear()
        command.upgrade(cfg, "0006")
        engine = create_engine(owner_url)
        try:
            with engine.begin() as c:
                c.execute(text("""
                    INSERT INTO users (id, email, name, password_hash,
                        is_platform_admin, created_at)
                    VALUES (:id, 'seed@mf005.example', 'Seed Operator', NULL,
                        true, now())
                """), {"id": seed_id})
        finally:
            engine.dispose()
        command.upgrade(cfg, "head")
        engine = create_engine(owner_url)
        try:
            with engine.begin() as c:
                assert c.scalar(text(
                    "SELECT count(*) FROM users WHERE id=:id"
                ), {"id": seed_id}) == 1
                assert c.scalar(text(
                    "SELECT to_regclass('public.platform_security_events')"
                )) == "platform_security_events"
                c.execute(text("""
                    INSERT INTO platform_security_events (
                        id, actor_user_id, action, outcome, target_type, reason_code
                    ) VALUES (:id, :actor, 'jobs.run', 'approved', 'job_queue', '')
                """), {"id": uuid.uuid4(), "actor": seed_id})
                assert c.scalar(text(
                    "SELECT count(*) FROM platform_security_events"
                )) == 1
        finally:
            engine.dispose()
    finally:
        os.environ["DATABASE_URL"] = original
        get_settings.cache_clear()
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


@pytest.mark.parametrize("route,form,action,target_type", [
    ("/operator/reviews/not-a-uuid/decide", {"decision": "approve"},
     "review.decide", "lesson_review"),
    ("/operator/skills/not-a-uuid/rollback", {},
     "skill.rollback", "shared_skill"),
])
def test_malformed_operator_targets_are_recorded_as_denied(
    platform, route, form, action, target_type,
):
    from app.db import session_scope
    from app.models import PlatformSecurityEvent
    operator, csrf, actor = _operator(platform)
    response = operator.post(route, data={"csrf": csrf, **form})
    assert response.status_code == 404
    with session_scope() as db:
        event = db.scalar(select(PlatformSecurityEvent).where(
            PlatformSecurityEvent.actor_user_id == actor,
            PlatformSecurityEvent.action == action,
            PlatformSecurityEvent.target_type == target_type,
            PlatformSecurityEvent.outcome == "denied",
            PlatformSecurityEvent.target_id.is_(None),
            PlatformSecurityEvent.reason_code == "not_found",
        ).order_by(PlatformSecurityEvent.created_at.desc()))
        assert event is not None


@pytest.mark.parametrize("outcome,reason", [
    ("approved", "not_found"),
    ("denied", ""),
])
def test_postgres_rejects_impossible_outcome_reason_pairs(pg_database, outcome, reason):
    from app.db import session_scope
    with pytest.raises(DBAPIError, match="ck_platform_security_outcome_reason"):
        with session_scope() as db:
            db.execute(text("""
                INSERT INTO platform_security_events
                  (id, actor_user_id, action, outcome, target_type, reason_code)
                VALUES (:id, :actor, 'jobs.run', :outcome, 'job_queue', :reason)
            """), {
                "id": uuid.uuid4(), "actor": uuid.uuid4(),
                "outcome": outcome, "reason": reason,
            })
