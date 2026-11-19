"""Launch a THROWAWAY local PostgreSQL container for MF-019 tests, then remove it.

This tool never reads .env, never connects to production, chooses a random
loopback port, creates a random one-use DB password and removes only its own
container after tests. Requires already installed local postgres:16-alpine image.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path


def invoke(*args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          check=False)


def main():
    root = Path(__file__).resolve().parents[1]
    name = "mf019-test-pg-" + secrets.token_hex(5)
    password = secrets.token_urlsafe(28)
    if not root.name == "mf_platform":
        raise RuntimeError("Not the MF canonical project root.")
    images = invoke("docker", "image", "inspect", "postgres:16-alpine")
    if images.returncode:
        print("Not run: postgres:16-alpine is not cached locally. No image was downloaded.")
        return 2
    started = False
    try:
        run = invoke("docker", "run", "-d", "--rm", "--name", name,
                     "-p", "127.0.0.1::5432",
                     "-e", "POSTGRES_PASSWORD=" + password,
                     "postgres:16-alpine", timeout=35)
        if run.returncode:
            print("Disposable PostgreSQL could not start; existing containers untouched.")
            return 2
        started = True
        port = invoke("docker", "port", name, "5432/tcp")
        host_port = port.stdout.strip()
        if port.returncode or not re.fullmatch(r"127\.0\.0\.1:\d{2,5}", host_port):
            print("The container did not publish a valid loopback-only port.")
            return 2
        ready = False
        for _ in range(30):
            status = invoke("docker", "exec", name, "pg_isready", "-U", "postgres", "-q", timeout=5)
            if status.returncode == 0:
                ready = True
                break
            time.sleep(.4)
        if not ready:
            print("Disposable test server failed readiness check.")
            return 2
        env = os.environ.copy()
        env["MF_TEST_PG_ADMIN_URL"] = (
            f"postgresql://postgres:{password}@127.0.0.1:{host_port.split(':')[-1]}/postgres")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["MF_ENV_FILE"] = ""
        target = ("tests/test_pg_migrations.py" if "--migrations" in sys.argv else
                  "tests" if "--all" in sys.argv else "tests/test_pg_visibility_storage.py")
        print("Running " + target + " on a disposable PostgreSQL 16 database.")
        test = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q",
             target, "-p", "no:cacheprovider"],
            cwd=root, env=env, capture_output=True, text=True, timeout=300)
        print(test.stdout.replace(password, "[REDACTED]"))
        if test.stderr:
            print(test.stderr.replace(password, "[REDACTED]"))
        return test.returncode
    finally:
        if started:
            invoke("docker", "stop", name, timeout=35)
            print("Stopped only MF-019's disposable PostgreSQL container.")


if __name__ == "__main__":
    raise SystemExit(main())
