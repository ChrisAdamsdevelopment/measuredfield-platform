"""Engine, sessions and the tenant context.

Tenant isolation is enforced in PostgreSQL, not only in Python. Every transaction
carries two transaction-local settings, ``app.org_id`` and ``app.user_id``. The
row-level security policies created by the migration compare each row's ``org_id``
with ``app.org_id``. With no context set the policies match nothing, so a code path
that forgets to establish a tenant sees zero rows instead of every tenant's rows.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Iterator

from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import TENANT_TABLES

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured.")
        _engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=TenantSession)
    return _engine


def reset_engine() -> None:
    """Used by tests that switch databases."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


class TenantSession(Session):
    """A Session that re-applies the tenant context at the start of every transaction."""

    def set_context(self, org_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> None:
        if self.in_transaction():
            # Pending rows belong to the context they were created under: write them
            # before the context changes, or row-level security will (rightly) refuse them.
            self.flush()
        self.info["org_id"] = str(org_id) if org_id else ""
        self.info["user_id"] = str(user_id) if user_id else ""
        if self.in_transaction():
            _apply_context(self, self.connection())

    def set_org(self, org_id: uuid.UUID | None) -> None:
        self.set_context(org_id, uuid.UUID(self.info["user_id"]) if self.info.get("user_id") else None)

    @property
    def org_id(self) -> uuid.UUID | None:
        value = self.info.get("org_id")
        return uuid.UUID(value) if value else None


def _apply_context(session: Session, connection) -> None:
    connection.execute(
        text("SELECT set_config('app.org_id', :o, true), set_config('app.user_id', :u, true)"),
        {"o": session.info.get("org_id", ""), "u": session.info.get("user_id", "")},
    )


@event.listens_for(TenantSession, "after_begin")
def _after_begin(session, transaction, connection):  # noqa: ARG001
    _apply_context(session, connection)


def new_session() -> TenantSession:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope(org_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> Iterator[TenantSession]:
    db = new_session()
    db.set_context(org_id, user_id)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db(request: Request) -> TenantSession:
    """FastAPI dependency: one session (one transaction) per request, with no tenant
    until authentication sets it. The HTTP middleware in main.py commits on success,
    rolls back on any 4xx/5xx and closes it, before the response is released, so a
    redirect never races the commit."""
    db = getattr(request.state, "db", None)
    if db is None:
        db = new_session()
        request.state.db = db
    return db


def finish_request_session(request: Request, *, success: bool) -> bool:
    """Commit or roll back the request session. Returns True if jobs were enqueued."""
    db = getattr(request.state, "db", None)
    if db is None:
        return False
    try:
        if success:
            db.commit()
        else:
            db.rollback()
        return bool(success and db.info.get("jobs_enqueued"))
    finally:
        db.close()
        request.state.db = None


def isolation_self_check() -> list[str]:
    """Return reasons the database cannot be trusted to isolate tenants. Empty = OK."""
    problems: list[str] = []
    with get_engine().connect() as conn:
        role = conn.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        if role.rolsuper or role.rolbypassrls:
            problems.append("The application database role can bypass row-level security "
                            "(superuser or BYPASSRLS). Connect as an ordinary role.")
        rows = conn.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'")).all()
        state = {r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in rows}
        for table in [*TENANT_TABLES, "organizations"]:
            if state.get(table) != (True, True):
                problems.append(f"Row-level security is not enabled and forced on '{table}'.")
    return problems
