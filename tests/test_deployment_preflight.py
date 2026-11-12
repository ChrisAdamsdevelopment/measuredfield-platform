"""Offline checks for worker and migration safety; never connect to production."""
from pathlib import Path

import pytest

from app.migrate import require_distinct_db_roles
from app import worker

ROOT = Path(__file__).resolve().parents[1]


def test_migration_credentials_must_be_distinct_and_same_database():
    runtime = "postgresql+psycopg://mf_app:fake@db.example.invalid:5432/mf_test"
    owner = "postgresql+psycopg://mf_owner:fake@db.example.invalid:5432/mf_test"
    require_distinct_db_roles(runtime, owner)
    for bad in (
        runtime,
        "postgresql+psycopg://mf_owner:fake@db.example.invalid:5432/other_db",
        "sqlite:///anything.db",
        "",
    ):
        with pytest.raises(ValueError):
            require_distinct_db_roles(runtime, bad)


def test_worker_refuses_inline_jobs(monkeypatch):
    from app.config import Settings
    s = Settings(
        env="test",
        DATABASE_URL="postgresql+psycopg://mf_app:fake@db.example.invalid:5432/mf_test",
        inline_jobs=True,
    )
    monkeypatch.setattr(worker, "get_settings", lambda: s)
    monkeypatch.setattr(worker, "isolation_self_check", lambda: [])
    with pytest.raises(RuntimeError, match="MF_INLINE_JOBS"):
        worker.startup_checks()


def test_worker_refuses_missing_database(monkeypatch):
    from app.config import Settings
    # Explicit validation alias and flag make this test deterministic even when
    # the PostgreSQL integration fixture temporarily sets DATABASE_URL and inline jobs.
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(
        env="test", DATABASE_URL="", inline_jobs=False))
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        worker.startup_checks()


def test_blueprint_is_staging_only_and_autodeploy_off():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "measuredfield-staging-web" in text
    assert "measuredfield-staging-worker" in text
    assert text.count('autoDeployTrigger: "off"') == 2
    assert "preDeployCommand: python -m app.migrate" in text
    assert "startCommand: python -m app.worker" in text
    assert "MIGRATION_DATABASE_URL" in text
    assert "MF_TRUST_PROXY_HEADERS" in text
    assert "--no-proxy-headers" in text
    assert "MF_PAYMENT_PROVIDER" in text
    assert "value: none" in text


def test_no_credentials_embedded_in_blueprint():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "postgresql://" not in text
    assert "sync: false" in text
    assert "generateValue: true" in text
