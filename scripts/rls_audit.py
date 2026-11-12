"""Destructive only to a specifically named disposable localhost PostgreSQL database.

Requires MF_AUDIT_ADMIN_URL and MF_AUDIT_RUNTIME_URL supplied by the caller.
Never reuse a customer/staging/production database. Does not create Docker containers.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]


def _validate(admin: str, runtime: str) -> None:
    from app.migrate import require_distinct_db_roles
    require_distinct_db_roles(runtime, admin)
    urls = [urlsplit(admin), urlsplit(runtime)]
    for url in urls:
        if url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise RuntimeError("RLS audit refuses all non-loopback databases.")
        if not url.path.startswith("/mf_audit_") or url.path != urls[0].path:
            raise RuntimeError("RLS audit requires the same mf_audit_* disposable database.")


def main() -> int:
    admin = os.environ.get("MF_AUDIT_ADMIN_URL", "")
    runtime = os.environ.get("MF_AUDIT_RUNTIME_URL", "")
    _validate(admin, runtime)
    from app.config import get_settings
    from app.db import isolation_self_check, reset_engine, session_scope
    from app.models import Lead, Membership, Organization, User, TENANT_TABLES

    # Migrations run as schema owner in this disposable database only.
    os.environ["DATABASE_URL"] = admin
    get_settings.cache_clear()
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    # Temporary role is created inside the disposable Docker PostgreSQL instance.
    from psycopg import connect, sql
    raw_admin = admin.replace("postgresql+psycopg://", "postgresql://", 1)
    password = urlsplit(runtime).password
    if not password:
        raise RuntimeError("Disposable runtime role password required")
    with connect(raw_admin, autocommit=True) as pg:
        pg.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier("mf_app"), sql.Literal(password)))
        pg.execute("ALTER ROLE mf_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
        pg.execute("GRANT USAGE ON SCHEMA public TO mf_app")
        pg.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mf_app")
        pg.execute("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO mf_app")
        pg.execute("REVOKE CREATE ON SCHEMA public FROM mf_app")

    os.environ["DATABASE_URL"] = runtime
    get_settings.cache_clear()
    reset_engine()

    violations = isolation_self_check()
    assert not violations, f"RLS self-check failed: {violations!r}"

    a, b = uuid.uuid4(), uuid.uuid4()
    with session_scope(org_id=a) as db:
        db.add(Organization(id=a, name="Synthetic Tenant A"))
        db.flush()
        lead_a = Lead(org_id=a, name="Demo A", email="a@example.invalid", message="A only")
        db.add(lead_a)
        db.flush()
        id_a = lead_a.id
    with session_scope(org_id=b) as db:
        db.add(Organization(id=b, name="Synthetic Tenant B"))
        db.flush()
        lead_b = Lead(org_id=b, name="Demo B", email="b@example.invalid", message="B only")
        db.add(lead_b)
        db.flush()
        id_b = lead_b.id

    with session_scope(org_id=a) as db:
        assert {l.id for l in db.scalars(select(Lead)).all()} == {id_a}
        assert db.get(Lead, id_b) is None, "Tenant A could read tenant B's lead"
        assert {x.id for x in db.scalars(select(Organization)).all()} == {a}
    with session_scope(org_id=b) as db:
        assert {l.id for l in db.scalars(select(Lead)).all()} == {id_b}
        assert db.get(Lead, id_a) is None, "Tenant B could read tenant A's lead"
        updated = db.execute(text("UPDATE leads SET status='won' WHERE id=:id"), {"id": id_a})
        assert updated.rowcount == 0, "Cross-tenant UPDATE succeeded"
        deleted = db.execute(text("DELETE FROM leads WHERE id=:id"), {"id": id_a})
        assert deleted.rowcount == 0, "Cross-tenant DELETE succeeded"
    with session_scope() as db:
        assert db.scalars(select(Lead)).all() == [], "No-tenant session exposed leads"
        assert db.scalars(select(Organization)).all() == [], "No-tenant session exposed organizations"
    with session_scope(org_id=b) as db:
        try:
            with db.begin_nested():
                db.add(Lead(org_id=a, name="Forged A", email="x@example.invalid", message="forged"))
                db.flush()
        except Exception as exc:
            # RLS WITH CHECK prevents cross-tenant INSERT, not application validation alone.
            assert "row-level security" in str(exc).lower(), type(exc).__name__
        else:
            raise AssertionError("Cross-tenant INSERT was accepted")

    with session_scope() as db:
        user = User(email="owner-a@example.invalid", name="Synthetic Member A")
        db.add(user)
        db.flush()
        uid = user.id
    with session_scope(org_id=a, user_id=uid) as db:
        db.add(Membership(org_id=a, user_id=uid, role="owner"))
    with session_scope(user_id=uid) as db:
        assert {m.org_id for m in db.scalars(select(Membership)).all()} == {a}
        assert {o.id for o in db.scalars(select(Organization)).all()} == {a}
    with session_scope(org_id=b, user_id=uid) as db:
        assert not db.scalars(select(Membership)).all(), "Member of A saw B memberships"
    print(f"POSTGRES_RLS_AUDIT_PASS: {len(TENANT_TABLES)} registered tenant table names checked by isolation_self_check")
    print("Verified separate role, FORCE RLS, A/B SELECT, cross UPDATE/DELETE/INSERT, no-context, and own-membership discovery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
