"""Backup and restore drill on a disposable, loopback-only PostgreSQL.

    python -B scripts/backup_drill.py

What it proves, with synthetic data only:

1. A database built exactly like production (schema-owner role runs migrations, a separate
   restricted role runs the app, forced row-level security on every tenant table) can be
   dumped with pg_dump and restored with pg_restore into a NEW database.
2. The restored database is row-for-row identical to the source (per-table row counts and
   content checksums), has the same schema revision, and still has row-level security
   enabled AND forced on every tenant table with the same policies.
3. The restricted application role still works against the restored copy, still passes the
   application's own isolation self-check, and still cannot see another tenant's rows.
4. WHO takes the dump matters: under FORCE ROW LEVEL SECURITY the schema owner sees no
   tenant rows. The drill records what pg_dump does when run as the owner, with and without
   --enable-row-security, so an operator never mistakes an empty dump for a backup.

It never touches an existing container, volume, database or credential. It starts its own
postgres:16-alpine container (already-cached image only, no pull), bound to 127.0.0.1 on a
random port with a random password, and removes it afterwards.

This is a LOGICAL backup drill. A managed provider's physical backups / point-in-time
recovery are a different mechanism and must be drilled on the provider (docs/STAGING_RUNBOOK.md).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
IMAGE = "postgres:16-alpine"
SOURCE_DB, RESTORED_DB = "mf_drill_source", "mf_drill_restored"
OWNER, RUNTIME = "mf_drill_owner", "mf_drill_app"


# --------------------------------------------------------------------------- pure checks

def table_fingerprints(conn) -> dict[str, dict]:
    """Row count and order-independent content checksum for every public table.

    Must be called on a connection that can see all rows (superuser or BYPASSRLS); a
    restricted role would fingerprint only what its tenant context shows.
    """
    from sqlalchemy import text
    tables = [r[0] for r in conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))]
    result = {}
    for table in tables:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise RuntimeError("unexpected table name")
        count, checksum = conn.execute(text(
            f"SELECT count(*), coalesce(md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))), 'empty') "  # noqa: S608
            f"FROM {table} t")).one()
        result[table] = {"rows": int(count), "checksum": checksum}
    return result


def rls_state(conn) -> dict[str, dict]:
    from sqlalchemy import text
    rows = conn.execute(text(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
        "FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' ORDER BY 1")).all()
    return {r[0]: {"enabled": bool(r[1]), "forced": bool(r[2]), "policies": int(r[3])} for r in rows}


def compare(source: dict, restored: dict) -> list[str]:
    """Human-readable differences between two fingerprint or RLS-state maps."""
    problems = []
    for name in sorted(set(source) | set(restored)):
        if name not in restored:
            problems.append(f"{name}: missing after restore")
        elif name not in source:
            problems.append(f"{name}: unexpected after restore")
        elif source[name] != restored[name]:
            problems.append(f"{name}: {source[name]} -> {restored[name]}")
    return problems


def unprotected_tenant_tables(state: dict[str, dict]) -> list[str]:
    from app.models import TENANT_TABLES
    return [t for t in [*TENANT_TABLES, "organizations"]
            if not (state.get(t, {}).get("enabled") and state.get(t, {}).get("forced") and state.get(t, {}).get("policies"))]


# ------------------------------------------------------------------------------- drill

def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=False, text=True, capture_output=True, **kwargs)


def _psql(container: str, database: str, sql: str) -> subprocess.CompletedProcess:
    return _run(["docker", "exec", container, "psql", "-U", "postgres", "-d", database,
                 "-v", "ON_ERROR_STOP=1", "-qAt", "-c", sql])


def _seed(runtime_url: str) -> dict:
    """Two synthetic tenants plus platform rows, written as the restricted app role."""
    os.environ["DATABASE_URL"] = runtime_url
    os.environ["MF_ENV_FILE"] = ""
    from app.config import get_settings
    from app.db import reset_engine, session_scope
    from app.models import (Assessment, AuditEvent, Customer, Document, Lead, LeadNote, Membership,
                            Organization, Site, SiteHost, SiteVersion, User, now)
    from app.security import digest, hash_password
    get_settings.cache_clear()
    reset_engine()
    ids = {}
    for key in ("alder", "birch"):
        org_id, user_id = uuid.uuid4(), uuid.uuid4()
        ids[key] = org_id
        with session_scope() as db:
            db.add(User(id=user_id, email=f"owner@{key}-drill.example", name=f"{key.title()} Owner",
                        password_hash=hash_password("synthetic drill passphrase")))
            db.add(Assessment(token_hash=digest(secrets.token_urlsafe(24)), business_name=f"{key.title()} Drill Co",
                              contact_name="Synthetic Person", email=f"owner@{key}-drill.example",
                              excited_text="Synthetic excited answer.", blocker_text="Synthetic blocker answer.",
                              result={"synthetic": True}, consent_at=now()))
        with session_scope(org_id=org_id, user_id=user_id) as db:
            db.add(Organization(id=org_id, name=f"{key.title()} Drill Co"))
            db.flush()
            db.add(Membership(org_id=org_id, user_id=user_id, role="owner"))
            site = Site(org_id=org_id, slug=f"{key}-drill", draft={"name": f"{key.title()} Drill Co"})
            db.add(site)
            db.flush()
            version = SiteVersion(org_id=org_id, site_id=site.id, number=1, content={"n": 1},
                                  html=f"<h1>{key} drill site — unicode é中</h1>", css="body{}")
            db.add(version)
            db.flush()
            site.published_version_id = version.id
            db.add(SiteHost(hostname=f"{key}-drill.sites.example", site_id=site.id, org_id=org_id, verified_at=now()))
            customer = Customer(org_id=org_id, name=f"{key.upper()}-CANARY customer")
            db.add(customer)
            db.flush()
            for n in range(25):
                lead = Lead(org_id=org_id, site_id=site.id, customer_id=customer.id if n == 0 else None,
                            name=f"{key.upper()}-CANARY visitor {n}", email=f"v{n}@{key}-drill.example",
                            message=f"{key.upper()}-CANARY message {n}\nwith a second line and 'quotes'")
                db.add(lead)
                db.flush()
                db.add(LeadNote(org_id=org_id, lead_id=lead.id, author_id=user_id, body=f"note {n}"))
            db.add(Document(org_id=org_id, filename="binary.bin", content_type="application/octet-stream",
                            size=4096, sha256="0" * 64, data=secrets.token_bytes(4096), uploaded_by=user_id))
            db.add(AuditEvent(org_id=org_id, actor_user_id=user_id, action="drill.seeded", meta={"rows": 25}))
    reset_engine()
    return ids


def _isolation_after_restore(runtime_url: str, ids: dict) -> list[str]:
    os.environ["DATABASE_URL"] = runtime_url
    from sqlalchemy import text
    from app.config import get_settings
    from app.db import isolation_self_check, reset_engine, session_scope
    get_settings.cache_clear()
    reset_engine()
    problems = list(isolation_self_check())
    try:
        with session_scope() as db:
            if db.execute(text("SELECT count(*) FROM leads")).scalar() != 0:
                problems.append("restored database shows tenant rows with no tenant context")
        with session_scope(org_id=ids["alder"]) as db:
            if db.execute(text("SELECT count(*) FROM leads")).scalar() != 25:
                problems.append("restricted role cannot read its own tenant's restored rows")
            if db.execute(text("SELECT count(*) FROM leads WHERE org_id = :b"), {"b": ids["birch"]}).scalar() != 0:
                problems.append("tenant A can see tenant B after restore")
            if db.execute(text("SELECT count(*) FROM leads WHERE name LIKE 'BIRCH-CANARY%'")).scalar() != 0:
                problems.append("tenant B canary visible to tenant A after restore")
            if db.execute(text("SELECT length(data) FROM documents")).scalar() != 4096:
                problems.append("binary document not intact after restore")
    finally:
        reset_engine()
    return problems


def main() -> int:
    if _run(["docker", "image", "inspect", IMAGE]).returncode:
        print("BACKUP_DRILL_ERROR: requires the cached Docker image postgres:16-alpine (no pull attempted).", file=sys.stderr)
        return 2
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    container = "mf-backup-drill-" + str(os.getpid())
    admin_pw, owner_pw, runtime_pw = (secrets.token_urlsafe(20) for _ in range(3))
    report: dict = {"drill": "logical pg_dump/pg_restore", "image": IMAGE, "synthetic_data_only": True, "steps": {}}
    started = False
    try:
        if _run(["docker", "run", "--rm", "-d", "--pull=never", "--name", container,
                 "--publish", f"127.0.0.1:{port}:5432", "-e", "POSTGRES_PASSWORD=" + admin_pw, IMAGE]).returncode:
            print("BACKUP_DRILL_ERROR: could not start the disposable PostgreSQL container.", file=sys.stderr)
            return 2
        started = True
        for _ in range(80):
            if _psql(container, "postgres", "SELECT 1").returncode == 0:
                break
            time.sleep(0.25)
        else:
            print("BACKUP_DRILL_ERROR: PostgreSQL did not become ready.", file=sys.stderr)
            return 2
        from sqlalchemy import create_engine, text

        def url(user: str, password: str, database: str) -> str:
            return f"postgresql+psycopg://{user}:{password}@127.0.0.1:{port}/{database}"

        setup = (f"CREATE ROLE {OWNER} LOGIN PASSWORD '{owner_pw}' NOSUPERUSER NOBYPASSRLS; "
                 f"CREATE ROLE {RUNTIME} LOGIN PASSWORD '{runtime_pw}' NOSUPERUSER NOBYPASSRLS;")
        assert _psql(container, "postgres", setup).returncode == 0
        assert _psql(container, "postgres", f"CREATE DATABASE {SOURCE_DB} OWNER {OWNER}").returncode == 0

        # 1. build the source exactly as a deployment does: migrate as owner, grant, run as app role
        env = dict(os.environ, DATABASE_URL=url(OWNER, owner_pw, SOURCE_DB), MF_ENV_FILE="")
        migrate = _run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(ROOT), env=env)
        if migrate.returncode:
            print("BACKUP_DRILL_ERROR: migrations failed on the source database.", file=sys.stderr)
            return 2
        grants = re.sub(r"--.*", "", (ROOT / "scripts" / "grant_runtime_role.sql").read_text(encoding="utf-8"))
        owner_engine = create_engine(url(OWNER, owner_pw, SOURCE_DB), isolation_level="AUTOCOMMIT")
        with owner_engine.connect() as conn:
            for statement in (s.strip() for s in grants.split(";")):
                if statement and not statement.upper().startswith("ALTER ROLE"):
                    conn.execute(text(statement.replace("mf_app", RUNTIME)))
        owner_engine.dispose()
        ids = _seed(url(RUNTIME, runtime_pw, SOURCE_DB))

        admin_source = create_engine(url("postgres", admin_pw, SOURCE_DB))
        with admin_source.connect() as conn:
            source_rows, source_rls = table_fingerprints(conn), rls_state(conn)
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        admin_source.dispose()
        report["steps"]["source"] = {"tables": len(source_rows), "rows": sum(t["rows"] for t in source_rows.values()),
                                     "schema_revision": revision, "leads": source_rows["leads"]["rows"]}

        # 2. the trap: what does a dump taken by the schema OWNER contain under FORCE RLS?
        owner_env = ["docker", "exec", "-e", "PGPASSWORD=" + owner_pw, container]
        strict = _run([*owner_env, "pg_dump", "-h", "127.0.0.1", "-U", OWNER, "-d", SOURCE_DB, "--data-only", "-t", "leads"])
        lax = _run([*owner_env, "pg_dump", "-h", "127.0.0.1", "-U", OWNER, "-d", SOURCE_DB, "--data-only", "-t", "leads",
                    "--enable-row-security"])
        lax_rows = len([line for line in lax.stdout.splitlines() if "CANARY" in line])
        report["steps"]["owner_role_dump"] = {
            "default_flags": "refused by pg_dump (good: fails loudly)" if strict.returncode else "SUCCEEDED",
            "default_flags_rows": len([line for line in strict.stdout.splitlines() if "CANARY" in line]),
            "with_enable_row_security": "exit 0" if lax.returncode == 0 else "failed",
            "with_enable_row_security_rows": lax_rows,
            "expected_rows": source_rows["leads"]["rows"],
            "conclusion": "A dump taken as the schema owner is NOT a backup of tenant data. "
                          "Back up with a superuser or a dedicated BYPASSRLS role.",
        }

        # 3. real backup (role that bypasses RLS) and restore into a brand-new database
        dump = _run(["docker", "exec", container, "pg_dump", "-U", "postgres", "-d", SOURCE_DB, "-Fc", "-f", "/tmp/mf_drill.dump"])
        size = _run(["docker", "exec", container, "sh", "-c", "wc -c < /tmp/mf_drill.dump"])
        assert _psql(container, "postgres", f"CREATE DATABASE {RESTORED_DB} OWNER {OWNER}").returncode == 0
        restore = _run(["docker", "exec", container, "pg_restore", "-U", "postgres", "-d", RESTORED_DB,
                        "--exit-on-error", "/tmp/mf_drill.dump"])
        report["steps"]["backup"] = {"pg_dump_exit": dump.returncode, "dump_bytes": int(size.stdout.strip() or 0),
                                     "pg_restore_exit": restore.returncode}
        if dump.returncode or restore.returncode:
            print(json.dumps(report, indent=2))
            print("BACKUP_DRILL_FAIL: pg_dump or pg_restore reported an error.")
            return 1

        # 4. verify the restored copy
        admin_restored = create_engine(url("postgres", admin_pw, RESTORED_DB))
        with admin_restored.connect() as conn:
            restored_rows, restored_rls = table_fingerprints(conn), rls_state(conn)
            restored_revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        admin_restored.dispose()
        problems = ["data: " + p for p in compare(source_rows, restored_rows)]
        problems += ["rls: " + p for p in compare(source_rls, restored_rls)]
        problems += ["rls missing on " + t for t in unprotected_tenant_tables(restored_rls)]
        if restored_revision != revision:
            problems.append("schema revision changed")
        if lax.returncode == 0 and lax_rows >= source_rows["leads"]["rows"]:
            problems.append("owner-role dump unexpectedly contained tenant rows: FORCE RLS is not effective for the owner")
        problems += ["isolation: " + p for p in _isolation_after_restore(url(RUNTIME, runtime_pw, RESTORED_DB), ids)]
        check = _run([sys.executable, "-m", "alembic", "check"], cwd=str(ROOT),
                     env=dict(os.environ, DATABASE_URL=url(OWNER, owner_pw, RESTORED_DB), MF_ENV_FILE=""))
        if check.returncode:
            problems.append("alembic check: restored schema does not match the models")
        report["steps"]["verify"] = {
            "tables_compared": len(restored_rows), "rows_compared": sum(t["rows"] for t in restored_rows.values()),
            "tenant_tables_with_forced_rls": len(restored_rls) - len(unprotected_tenant_tables(restored_rls)) - sum(
                1 for t in restored_rls.values() if not t["forced"]),
            "schema_revision": restored_revision, "problems": problems}
        print(json.dumps(report, indent=2))
        if problems:
            print(f"BACKUP_DRILL_FAIL: {len(problems)} problem(s).")
            return 1
        print(f"BACKUP_DRILL_PASS: {report['steps']['verify']['rows_compared']} rows in "
              f"{report['steps']['verify']['tables_compared']} tables restored identically; RLS and isolation intact.")
        return 0
    finally:
        if started:
            stopped = _run(["docker", "stop", container])
            print("Disposable PostgreSQL container stopped and removed: " +
                  ("yes" if stopped.returncode == 0 else "FAILED - inspect Docker"), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
