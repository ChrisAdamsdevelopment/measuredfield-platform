"""Security-scan regression tests. All credential strings are synthetic only."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts.secret_scan import (
    _blocked_path,
    _safe_relative,
    scan_bytes,
    scan_repository,
)


def _synthetic_provider_tokens():
    # Split prefixes prevent accidental matching by the repository-level scan.
    return {
        "openai_api_key": ("sk-" + "proj-" + "Q" * 30).encode(),
        "anthropic_api_key": ("sk-" + "ant-api" + "B" * 30).encode(),
        "github_classic_token": ("gh" + "p_" + "a" * 36).encode(),
        "github_fine_grained_token": ("github_" + "pat_" + "b" * 40).encode(),
        "stripe_live_secret": ("sk_" + "live_" + "c" * 28).encode(),
        "stripe_signing_secret": ("wh" + "sec_" + "d" * 28).encode(),
        "google_api_key": ("AI" + "za" + "E" * 36).encode(),
        "aws_access_key": ("AK" + "IA" + "F" * 16).encode(),
    }


@pytest.mark.parametrize("rule", list(_synthetic_provider_tokens()))
def test_detects_common_token_patterns(rule):
    token = _synthetic_provider_tokens()[rule]
    results = scan_bytes("app/sample.py", b"credential = '" + token + b"'")
    assert any(x.rule == rule for x in results)
    assert all(token.decode() not in str(x) for x in results)


def test_private_key_and_database_url_detected_without_leaking_values():
    header = ("-----BEGIN " + "PRIVATE KEY-----").encode()
    results = scan_bytes("config/example.txt", b"x\n" + header + b"\n")
    assert results and results[0].line == 2
    assert results[0].rule == "private_key_header"
    assert header.decode() not in str(results)
    uri = b"postgresql://somebody:" + b"F" * 32 + b"@localhost:5432/database"
    result = scan_bytes("scripts/example.txt", uri)
    assert result and result[0].rule == "credential_in_url"
    assert b"F" * 32 not in str(result).encode()


@pytest.mark.parametrize("name, expected", [
    (".env", "tracked_runtime_environment"),
    (".env.production", "tracked_runtime_environment"),
    (".ENV.local", "tracked_runtime_environment"),
    ("app/credentials.json", "prohibited_sensitive_filename"),
    ("app/token.json", "prohibited_sensitive_filename"),
    ("id_ed25519", "prohibited_sensitive_filename"),
    ("private/key.pem", "prohibited_private_key_file"),
    ("app/secret.private.json", "prohibited_sensitive_filename"),
    (".env.example", None),
    ("docs/sample.json", None),
    ("README.md", None),
])
def test_sensitive_filenames_are_blocked(name, expected):
    assert _blocked_path(name) == expected


def test_binary_and_utf8_error_are_scanned_without_crashing():
    key = ("gh" + "p_" + "a" * 36).encode()
    result = scan_bytes("assets/sample.bin", b"\x00\xff" + key + b"\x00")
    assert any(x.rule == "github_classic_token" for x in result)


def test_unsafe_paths_refused():
    for path in ("../.env", "/etc/passwd", "a/../../token.json", "", "a//b.txt"):
        assert not _safe_relative(path)
        assert scan_bytes(path, b"hello")[0].rule == "unsafe_path"


def test_large_files_fail_closed():
    results = scan_bytes("data/large.txt", b"A" * (3 * 1024 * 1024 + 1))
    assert results[0].rule == "oversized_unscanned_file"


def test_innocent_synthetic_docs_and_example_urls_are_not_secret_findings():
    for content in (
        b"Never commit your api key, private key, or password",
        b"postgresql://postgres:synthetic-example-password@127.0.0.1:5432/postgres",
        b"https://user:test-example-password@example.invalid",
        b"nothing credential-like here",
    ):
        assert scan_bytes("docs/example.md", content) == []


def _git(root: Path, *args: str):
    subprocess.run(["git", "-C", str(root), *args],
                   check=True, capture_output=True)


def test_tracks_index_and_detects_uncommitted_credential_file():
    with tempfile.TemporaryDirectory(prefix="mf_secret_scan_") as directory:
        root = Path(directory)
        _git(root, "init", "-q")
        safe_file = root / "clean.py"
        safe_file.write_text("print('synthetic sample')\n", encoding="utf-8")
        _git(root, "add", "clean.py")
        issues, scanned = scan_repository(root, "staged")
        assert scanned == 1 and not issues

        sensitive = root / ".env.production"
        sensitive.write_text("EXAMPLE=synthetic_value_only\n", encoding="utf-8")
        _git(root, "add", ".env.production")
        issues, count = scan_repository(root, "staged")
        assert count == 2
        assert any(f.rule == "tracked_runtime_environment" for f in issues)
        assert all("synthetic_value_only" not in str(f) for f in issues)

        # A staged secret is read from Git index, not from a later overwritten file.
        s = root / "new.txt"
        s.write_bytes(("gh" + "p_" + "a" * 36).encode())
        _git(root, "add", "new.txt")
        s.write_text("clean after staging", encoding="utf-8")
        issues, _ = scan_repository(root, "staged")
        assert any(f.path == "new.txt" and f.rule == "github_classic_token"
                   for f in issues)


def test_ci_workflow_checks_secrets_before_install_and_database_tests():
    text = (Path(__file__).resolve().parents[1] /
            ".github/workflows/ci.yml").read_text(encoding="utf-8")
    scan = "python -B scripts/secret_scan.py --tracked"
    assert scan in text
    assert text.index(scan) < text.index("python -B scripts/ci_full_check.py")


def test_repo_scan_refuses_nonroot_path_and_incomplete_git_view():
    with tempfile.TemporaryDirectory(prefix="mf_secret_scan_root_") as directory:
        root = Path(directory)
        _git(root, "init", "-q")
        sub = root / "nested"
        sub.mkdir()
        (sub / "sample.py").write_text("print('safe')", encoding="utf-8")
        _git(root, "add", "nested/sample.py")
        with pytest.raises(RuntimeError, match="repository root"):
            scan_repository(sub, "tracked")


def test_cli_exit_code_and_output_hide_credential_values():
    import sys
    with tempfile.TemporaryDirectory(prefix="mf_secret_scan_cli_") as directory:
        root = Path(directory)
        _git(root, "init", "-q")
        token = "gh" + "p_" + "C" * 36
        (root / "synthetic-key.txt").write_text(token, encoding="utf-8")
        _git(root, "add", "synthetic-key.txt")
        script = Path(__file__).resolve().parents[1] / "scripts/secret_scan.py"
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--staged", "--root", str(root)],
            capture_output=True, text=True, check=False)
        assert result.returncode == 1
        assert "github_classic_token" in result.stdout
        assert "SECRET_SCAN_FAIL" in result.stdout
        assert token not in result.stdout
        assert token not in result.stderr
