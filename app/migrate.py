"""Fail-closed Alembic migration entrypoint using separate privileged credentials.

Requires MIGRATION_DATABASE_URL (owner/migrator) and DATABASE_URL (runtime role).
Never puts the privileged URL in logs, worker configuration or runtime connection pools.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit


def require_distinct_db_roles(runtime: str, migration: str) -> None:
    if not runtime or not migration:
        raise ValueError("Both DATABASE_URL and MIGRATION_DATABASE_URL are required.")
    r, m = urlsplit(runtime), urlsplit(migration)
    allowed = {"postgres", "postgresql", "postgresql+psycopg"}
    if r.scheme not in allowed or m.scheme not in allowed:
        raise ValueError("Database URLs must use PostgreSQL.")
    if not r.username or not m.username or r.username == m.username:
        raise ValueError("Migration and runtime database usernames must be different.")
    if not r.path or not m.path or r.path != m.path:
        raise ValueError("Migration and runtime URLs must target the same database name.")


def main() -> int:
    runtime = os.getenv("DATABASE_URL", "")
    migration = os.getenv("MIGRATION_DATABASE_URL", "")
    require_distinct_db_roles(runtime, migration)
    # Only inside this separate pre-deploy process may the migrator connect as owner.
    os.environ["DATABASE_URL"] = migration
    from alembic import command
    from alembic.config import Config
    from .config import get_settings
    get_settings.cache_clear()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    print("Migrations applied. Runtime must use the separate restricted DATABASE_URL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
