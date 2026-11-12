"""Fail-closed CI gate: a skipped PostgreSQL test must never look like a pass.

Runs on a disposable, explicitly configured localhost PostgreSQL test server.
Produces sanitized JUnit XML under ignored test-results/.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "test-results" / "pytest.xml"


def require_local_test_database(url: str) -> None:
    if not url:
        raise ValueError("MF_TEST_PG_ADMIN_URL missing: refusing to skip all PostgreSQL tests")
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgres", "postgresql", "postgresql+psycopg"):
        raise ValueError("Only PostgreSQL is supported for integration tests")
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("CI checks require a disposable loopback-only PostgreSQL service")
    if not parsed.path or parsed.path == "/":
        raise ValueError("Test PostgreSQL database must be explicitly named")


def check_junit_summary(xml: str, *, minimum_tests: int = 100) -> dict:
    root = ET.fromstring(xml)
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    if not suites:
        raise RuntimeError("pytest did not produce a JUnit test suite")
    counts = {name: sum(int(s.get(name, "0")) for s in suites)
              for name in ("tests", "skipped", "errors", "failures")}
    if (counts["tests"] < minimum_tests or counts["skipped"] or
            counts["errors"] or counts["failures"]):
        raise RuntimeError(f"CI full-coverage gate failed: {counts}")
    return counts


def main() -> int:
    require_local_test_database(os.getenv("MF_TEST_PG_ADMIN_URL", ""))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-B", "-m", "pytest", "tests",
            "-q", "-p", "no:cacheprovider", "--junitxml=" + str(REPORT)]
    result = subprocess.run(args, cwd=ROOT, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    counts = check_junit_summary(REPORT.read_text(encoding="utf-8"))
    print(f"CI_GATE_PASS: {counts['tests']} real PG-backed tests, "
          "zero skipped, zero errors, zero failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
