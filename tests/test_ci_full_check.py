"""Ensure CI cannot misreport a skipped PostgreSQL suite as a success."""
from xml.etree.ElementTree import ParseError

import pytest

from scripts.ci_full_check import check_junit_summary, require_local_test_database


def _xml(tests=102, skipped=0, errors=0, failures=0):
    return (f'<testsuites><testsuite tests="{tests}" skipped="{skipped}" '
            f'errors="{errors}" failures="{failures}" /></testsuites>')


def test_ci_gate_accepts_complete_database_suite():
    assert check_junit_summary(_xml()) == {
        "tests": 102, "skipped": 0, "errors": 0, "failures": 0
    }


@pytest.mark.parametrize("values", [
    {"tests": 99}, {"skipped": 1}, {"errors": 1}, {"failures": 1}
])
def test_ci_gate_rejects_partial_or_failed_execution(values):
    with pytest.raises(RuntimeError, match="CI full-coverage gate failed"):
        check_junit_summary(_xml(**values))


@pytest.mark.parametrize("url", [
    "", "sqlite:///temporary.db", "postgresql://localhost/",
    "postgresql://postgres:fake@db.example.invalid:5432/real",
])
def test_ci_gate_rejects_missing_or_nonlocal_database(url):
    with pytest.raises(ValueError):
        require_local_test_database(url)


def test_ci_gate_accepts_explicit_disposable_loopback_postgres():
    require_local_test_database(
        "postgresql://postgres:synthetic@127.0.0.1:55432/postgres"
    )
