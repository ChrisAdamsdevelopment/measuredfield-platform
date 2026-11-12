"""Migrations must work on a database that already contains tenant data.

Regression for a defect found on 2026-11-11: migration 0003 added foreign keys whose
validation query runs as the table owner. With FORCE ROW LEVEL SECURITY the owner sees no
rows, so the upgrade failed on any populated database while passing on empty test databases.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("MF_TEST_PG_ADMIN_URL", "")


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=url, MF_ENV_FILE="")
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True)


@pytest.mark.skipif(not ADMIN_URL, reason="MF_TEST_PG_ADMIN_URL is not set")
def test_upgrade_and_downgrade_on_a_populated_database():
    from sqlalchemy import create_engine, text
    admin_url = re.sub(r"^postgres(ql)?(\+psycopg)?://", "postgresql+psycopg://", ADMIN_URL)
    suffix = secrets.token_hex(4)
    dbname, owner, password = f"mf_mig_{suffix}", f"mf_mig_owner_{suffix}", secrets.token_hex(12)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE {owner} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"CREATE DATABASE {dbname} OWNER {owner}"))
    url = re.sub(r"//[^@]+@", f"//{owner}:{password}@", admin_url)
    url = re.sub(r"/[^/]+$", "/" + dbname, url)
    try:
        assert _alembic(url, "upgrade", "0002").returncode == 0
        org, site, version, customer, lead = (uuid.uuid4() for _ in range(5))
        engine = create_engine(url)
        with engine.begin() as conn:      # the owner is under FORCE RLS too: set a tenant context
            conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org)})
            conn.execute(text("INSERT INTO organizations (id, name, industry, plan, status, learning_opt_in, settings, created_at) "
                              "VALUES (:o, 'Populated Test Co', 'other', 'none', 'active', false, '{}', now())"), {"o": org})
            conn.execute(text("INSERT INTO sites (id, org_id, slug, draft, created_at, updated_at) "
                              "VALUES (:s, :o, :slug, '{}', now(), now())"), {"s": site, "o": org, "slug": "populated-" + suffix})
            conn.execute(text("INSERT INTO site_versions (id, org_id, site_id, number, content, html, css, note, created_at) "
                              "VALUES (:v, :o, :s, 1, '{}', '<p>x</p>', '', '', now())"), {"v": version, "o": org, "s": site})
            conn.execute(text("UPDATE sites SET published_version_id = :v WHERE id = :s"), {"v": version, "s": site})
            conn.execute(text("INSERT INTO site_hosts (hostname, site_id, org_id, kind, verified_at, created_at) "
                              "VALUES (:h, :s, :o, 'subdomain', now(), now())"), {"h": f"populated-{suffix}.sites.test", "s": site, "o": org})
            conn.execute(text("INSERT INTO customers (id, org_id, name, email, phone, notes, created_at) "
                              "VALUES (:c, :o, 'Pat', '', '', '', now())"), {"c": customer, "o": org})
            conn.execute(text("INSERT INTO leads (id, org_id, site_id, customer_id, name, email, phone, service, timing, message, source, status, created_at) "
                              "VALUES (:l, :o, :s, :c, 'Riley', 'r@r.example', '', '', '', 'hi', 'website', 'new', now())"),
                         {"l": lead, "o": org, "s": site, "c": customer})
            # Pre-0004 state: usable sign-in links sitting in stored email bodies.
            import hashlib
            live, spent = "LiveInviteToken_" + secrets.token_urlsafe(24), "SpentResetToken_" + secrets.token_urlsafe(24)
            for token, purpose, used in ((live, "invite", None), (spent, "reset", "now()")):
                conn.execute(text(
                    "INSERT INTO auth_tokens (id, token_hash, purpose, email, org_id, role, created_at, expires_at, used_at) "
                    f"VALUES (gen_random_uuid(), :h, :p, 'legacy@populated.example', :o, 'owner', now(), now() + interval '3 days', {used or 'NULL'})"),
                    {"h": hashlib.sha256(token.encode()).hexdigest(), "p": purpose, "o": org})
            conn.execute(text(
                "INSERT INTO email_outbox (id, org_id, kind, to_email, subject, body, status, provider, created_at) VALUES "
                "(gen_random_uuid(), :o, 'invite', 'legacy@populated.example', 'Invite', :b1, 'not_sent_no_provider', 'none', now()), "
                "(gen_random_uuid(), NULL, 'password_reset', 'legacy@populated.example', 'Reset', :b2, 'sent', 'resend', now()), "
                "(gen_random_uuid(), :o, 'lead_notice', 'legacy@populated.example', 'Notice', 'Sign in to read it: https://app.example/app/inquiries/1', 'queued', 'none', now())"),
                {"o": org, "b1": f"Set up here:\nhttps://app.example/accept-invite?token={live}\n\nThanks",
                 "b2": f"Reset: https://app.example/reset?next=1&token={spent} (expires soon)"})
        upgraded = _alembic(url, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr[-1500:]
        with engine.connect() as conn:
            # 0004 data repair: no stored body keeps a token, and every exposed unused token is revoked.
            bodies = [row[0] for row in conn.execute(text("SELECT body FROM email_outbox ORDER BY kind"))]
            assert len(bodies) == 3 and not any(live in b or spent in b for b in bodies)
            assert sum("token={{AUTH_LINK_TOKEN}}" in b for b in bodies) == 2
            assert any(b.endswith("\n\nThanks") for b in bodies) and any("(expires soon)" in b for b in bodies)
            assert any(b == "Sign in to read it: https://app.example/app/inquiries/1" for b in bodies)  # untouched
            live_hash = hashlib.sha256(live.encode()).hexdigest()
            assert conn.execute(text("SELECT count(*) FROM auth_tokens WHERE token_hash = :h"), {"h": live_hash}).scalar() == 0
            unused = conn.execute(text("SELECT expires_at <= now(), issued_at, issue_count, length(token_hash) "
                                       "FROM auth_tokens WHERE used_at IS NULL")).one()
            assert tuple(unused) == (True, None, 0, 64)
            assert conn.execute(text("SELECT count(*) FROM auth_tokens WHERE used_at IS NOT NULL")).scalar() == 1
            forced = dict(conn.execute(text(
                "SELECT relname, relforcerowsecurity FROM pg_class WHERE relname IN "
                "('sites','site_versions','organizations','leads','customers')")).all())
            assert forced and all(forced.values()), forced          # FORCE was restored
            assert conn.execute(text("SELECT count(*) FROM sites")).scalar() == 0   # and is effective again
        assert _alembic(url, "check").returncode == 0               # models and migrations agree
        assert _alembic(url, "downgrade", "0002").returncode == 0
        assert _alembic(url, "upgrade", "head").returncode == 0
        engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"))
            conn.execute(text(f"DROP ROLE IF EXISTS {owner}"))
        admin.dispose()
