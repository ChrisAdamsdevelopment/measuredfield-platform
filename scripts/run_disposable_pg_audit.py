"""Create and remove an isolated loopback PostgreSQL 16 Docker container for RLS audit.

Never touches existing containers, database volumes, public ports, or live credentials.
Only uses a cached postgres:16-alpine image; Docker network pulls are disabled.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "postgres:16-alpine"


def run(args: list[str], **kwargs):
    return subprocess.run(args, check=False, text=True, **kwargs)


def main() -> int:
    cached = run(["docker", "image", "inspect", IMAGE], capture_output=True)
    if cached.returncode:
        raise SystemExit("Audit requires cached Docker image postgres:16-alpine; no image pull attempted.")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    name = "mf-rls-audit-" + str(os.getpid())
    dbname = "mf_audit_disposable"
    password = secrets.token_urlsafe(20)
    started = False
    try:
        command = [
            "docker", "run", "--rm", "-d", "--pull=never",
            "--name", name, "--publish", f"127.0.0.1:{port}:5432",
            "-e", "POSTGRES_PASSWORD=" + password,
            "-e", "POSTGRES_DB=" + dbname,
            IMAGE,
        ]
        result = run(command, capture_output=True)
        if result.returncode:
            print("Unable to start disposable PostgreSQL container.", file=sys.stderr)
            return 2
        started = True
        print("Disposable PostgreSQL started on local loopback only.", flush=True)
        for _ in range(50):
            result = run(["docker", "exec", name, "pg_isready", "-q",
                          "-U", "postgres", "-d", dbname], capture_output=True)
            if result.returncode == 0:
                break
            time.sleep(0.25)
        else:
            print("Disposable PostgreSQL did not become ready.", file=sys.stderr)
            return 3
        env = os.environ.copy()
        env["MF_AUDIT_ADMIN_URL"] = (
            f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/{dbname}")
        env["MF_AUDIT_RUNTIME_URL"] = (
            f"postgresql+psycopg://mf_app:{password}@127.0.0.1:{port}/{dbname}")
        if "--full-suite" in sys.argv[1:]:
            # The pytest fixture uses the local postgres admin database to create
            # fresh owner/runtime roles and per-suite synthetic test databases.
            env["MF_TEST_PG_ADMIN_URL"] = (
                f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/postgres")
            print("Running full local suite with disposable real PostgreSQL.", flush=True)
            # Identical gate to GitHub CI: every PostgreSQL suite must execute,
            # the JUnit report must contain no skips, and 100+ tests must run.
            result = run([sys.executable, "-B", "scripts/ci_full_check.py"],
                         cwd=str(ROOT), env=env)
            print("FULL_POSTGRES_SUITE_EXIT=" + str(result.returncode), flush=True)
        else:
            print("Running migrations and synthetic two-business RLS audit.", flush=True)
            result = run([sys.executable, "-B", "-m", "scripts.rls_audit"],
                         cwd=str(ROOT), env=env)
            print("RLS_AUDIT_EXIT=" + str(result.returncode), flush=True)
        return result.returncode
    finally:
        if started:
            result = run(["docker", "stop", name], capture_output=True)
            print("Disposable PostgreSQL container stopped and removed: " +
                  ("yes" if result.returncode == 0 else "FAILED - inspect Docker"), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
