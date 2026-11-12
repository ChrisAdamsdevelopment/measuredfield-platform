"""Shared fixtures for the real-PostgreSQL integration suite (tests/test_pg_*.py).

These tests need a throwaway PostgreSQL server and are skipped unless
MF_TEST_PG_ADMIN_URL points at a superuser on localhost, e.g.

    MF_TEST_PG_ADMIN_URL=postgresql://postgres:devonly@127.0.0.1:55432/postgres

For each test session the fixture creates a brand-new database, runs the Alembic
migrations as a schema OWNER role, applies scripts/grant_runtime_role.sql, and then runs
the application as a separate, non-owner, NOBYPASSRLS role. That is the same two-role
topology the deployment guide requires, so row-level security is exercised for real.
Everything is fictional; the database is dropped afterwards.
"""
from __future__ import annotations

import os
import re
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# Must be set before any app module is imported: tests never read a developer's .env.
os.environ["MF_ENV_FILE"] = ""

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("MF_TEST_PG_ADMIN_URL", "")
SITES_DOMAIN = "sites.localhost"
_ENV_KEYS = ("DATABASE_URL", "MF_ENV", "MF_INLINE_JOBS", "MF_SECRET_KEY", "MF_DELETION_GRACE_HOURS",
             "MF_PAYMENT_PROVIDER", "MF_STRIPE_SECRET_KEY", "MF_STRIPE_WEBHOOK_SECRET", "MF_EMAIL_PROVIDER")


def _sa(url: str) -> str:
    return re.sub(r"^postgres(ql)?(\+psycopg)?://", "postgresql+psycopg://", url)


@pytest.fixture(scope="session")
def pg_database():
    if not ADMIN_URL:
        pytest.skip("MF_TEST_PG_ADMIN_URL is not set; real-PostgreSQL tests skipped")
    parts = urlsplit(ADMIN_URL)
    if parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip("Integration tests refuse non-loopback databases")
    from sqlalchemy import create_engine, text

    suffix = secrets.token_hex(4)
    dbname, owner, runtime = f"mf_test_{suffix}", f"mf_owner_{suffix}", f"mf_rt_{suffix}"
    owner_pw, runtime_pw = secrets.token_hex(12), secrets.token_hex(12)
    admin = create_engine(_sa(ADMIN_URL), isolation_level="AUTOCOMMIT")
    hostport = f"{parts.hostname}:{parts.port or 5432}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE {owner} LOGIN PASSWORD '{owner_pw}' NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"CREATE ROLE {runtime} LOGIN PASSWORD '{runtime_pw}' NOSUPERUSER NOBYPASSRLS"))
        conn.execute(text(f"CREATE DATABASE {dbname} OWNER {owner}"))
    owner_url = f"postgresql+psycopg://{owner}:{owner_pw}@{hostport}/{dbname}"
    runtime_url = f"postgresql+psycopg://{runtime}:{runtime_pw}@{hostport}/{dbname}"

    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    from app.config import get_settings
    from app.db import reset_engine

    # 1. migrate as the schema owner
    os.environ["DATABASE_URL"] = owner_url
    get_settings.cache_clear()
    reset_engine()
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    # 2. grant the runtime role exactly what the deployment script grants
    owner_engine = create_engine(owner_url, isolation_level="AUTOCOMMIT")
    grants = (ROOT / "scripts" / "grant_runtime_role.sql").read_text(encoding="utf-8")
    statements = [s.strip() for s in re.sub(r"--.*", "", grants).split(";") if s.strip()]
    with owner_engine.connect() as conn:
        for statement in statements:
            if statement.upper().startswith("ALTER ROLE"):
                continue  # role attributes are set by the admin above
            conn.execute(text(statement.replace("mf_app", runtime)))
    owner_engine.dispose()
    # 3. run the application as the restricted role
    os.environ.update({
        "DATABASE_URL": runtime_url, "MF_ENV": "test", "MF_INLINE_JOBS": "true",
        "MF_SECRET_KEY": "test-secret-" + secrets.token_hex(16), "MF_DELETION_GRACE_HOURS": "0",
        "MF_PAYMENT_PROVIDER": "none", "MF_EMAIL_PROVIDER": "none"})
    get_settings.cache_clear()
    reset_engine()
    try:
        yield {"runtime_url": runtime_url, "owner_url": owner_url, "dbname": dbname}
    finally:
        reset_engine()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)"))
            conn.execute(text(f"DROP ROLE IF EXISTS {runtime}"))
            conn.execute(text(f"DROP ROLE IF EXISTS {owner}"))
        admin.dispose()


@pytest.fixture(scope="session")
def platform(pg_database):
    from app.main import create_app
    return create_app()


@pytest.fixture(autouse=True)
def _fresh_rate_limits(request):
    """Every test client shares one socket address, so limits are reset between tests.
    Tests that exercise a limit do so within a single test."""
    if {"platform", "pg_database", "world"} & set(request.fixturenames):
        request.getfixturevalue("pg_database")
        from sqlalchemy import text
        from app.db import session_scope
        with session_scope() as db:
            db.execute(text("DELETE FROM rate_limits"))
    yield


def client_for(app, host: str = "testserver"):
    from starlette.testclient import TestClient
    return TestClient(app, base_url=f"http://{host}", follow_redirects=False)


def csrf_of(client) -> str:
    """Read the CSRF token the workspace embeds in its forms."""
    page = client.get("/app/settings")
    if page.status_code != 200:
        page = client.get("/operator")
    match = re.search(r'name="csrf" value="([0-9a-f]{64})"', page.text)
    assert match, "no CSRF token on page (status %s)" % page.status_code
    return match.group(1)


def last_email_link(to: str, path: str) -> str:
    """Pull a tokenized link out of the outbox, the way an operator relays it by hand."""
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import EmailMessage
    with session_scope() as db:
        message = db.scalars(select(EmailMessage).where(EmailMessage.to_email == to)
                             .order_by(EmailMessage.created_at.desc())).first()
        assert message is not None, "no outbox message for " + to
        assert message.status in ("queued", "not_sent_no_provider"), message.status
        match = re.search(r"https?://[^\s]+(" + re.escape(path) + r"\?token=[A-Za-z0-9_\-]+)", message.body)
        assert match, "no link in message body"
        return match.group(1)


def make_operator(email: str = "operator@measuredfield.test", password: str = "operator-test-passphrase"):
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import User
    from app.security import hash_password
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(User(email=email, name="Olive Operator", password_hash=hash_password(password),
                        is_platform_admin=True))
    return email, password


def login(app, email: str, password: str):
    client = client_for(app)
    response = client.post("/login", data={"email": email, "password": password, "next": "/app"})
    assert response.status_code == 303, response.text[:300]
    return client


def onboard_business(app, *, business: str, owner_email: str, owner_name: str = "Owner Person",
                     password: str = "correct horse battery staple", offer: str = "consult",
                     excited: str = "We just launched a deep cleaning service and customers love it.",
                     blocker: str = "I lose track of inquiries and forget to follow up, and our website looks dated."):
    """Fictional prospect -> assessment -> approval -> operator provisioning -> owner signed in.

    Returns (owner_client, org_id, assessment_token).
    """
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import ServiceOrder

    visitor = client_for(app)
    response = visitor.post("/assessment", data={
        "excited": excited, "blocker": blocker, "business_name": business, "contact_name": owner_name,
        "email": owner_email, "website": "", "industry": "cleaning", "consent": "yes"})
    assert response.status_code == 303, response.text[:400]
    token = response.headers["location"].rsplit("/", 1)[1]
    assert visitor.get(f"/assessment/r/{token}").status_code == 200
    response = visitor.post(f"/assessment/r/{token}/order",
                            data={"offer_code": offer, "approved_name": owner_name, "agree": "yes"})
    assert response.status_code == 303, response.text[:400]
    order_id = uuid.UUID(response.headers["location"].split("/order/")[1].split("?")[0])

    op_email, op_password = make_operator()
    op = login(app, op_email, op_password)
    response = op.post(f"/operator/orders/{order_id}/provision",
                       data={"csrf": csrf_of(op), "reason": "Fictional test customer; free consult"})
    assert response.status_code == 303, response.text[:400]
    with session_scope() as db:
        order = db.get(ServiceOrder, order_id)
        assert order.status == "provisioned" and order.org_id is not None
        org_id = order.org_id
        assert db.scalar(select(ServiceOrder.paid_at).where(ServiceOrder.id == order_id)) is None

    invite = last_email_link(owner_email, "/accept-invite")
    owner = client_for(app)
    assert owner.get(invite).status_code == 200
    response = owner.post("/accept-invite", data={"token": invite.split("token=")[1], "name": owner_name,
                                                  "password": password})
    assert response.status_code == 303, response.text[:400]
    assert owner.get("/app").status_code == 200
    return owner, org_id, token


def create_and_publish_site(owner, *, name: str, slug: str, city: str = "Fictionville, AZ"):
    csrf = csrf_of(owner)
    response = owner.post("/app/website/save", data={
        "csrf": csrf, "name": name, "city": city, "industry": "cleaning", "color": "ocean",
        "email": "hello@" + slug + ".example", "phone": "(555) 010-0199", "about": "A fictional test business.",
        "headline": "", "intro": "", "response_window": "no_promise",
        "services": "Deep cleaning | Top to bottom\nMove-out cleaning", "structured_intake": "on", "slug": slug})
    assert response.status_code == 303, response.text[:400]
    assert "Draft+saved" in response.headers["location"] or "mf_notice" in response.headers["location"]
    assert owner.get("/app/website/preview/").status_code == 200
    assert owner.post("/app/website/version", data={"csrf": csrf, "note": "First version"}).status_code == 303
    page = owner.get("/app/website").text
    version_id = re.search(r"/app/website/versions/([0-9a-f-]{36})/approve", page).group(1)
    assert owner.post(f"/app/website/versions/{version_id}/approve", data={"csrf": csrf}).status_code == 303
    assert owner.post(f"/app/website/versions/{version_id}/publish", data={"csrf": csrf}).status_code == 303
    return f"{slug}.{SITES_DOMAIN}", version_id
