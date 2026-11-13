"""Local-only, redacted secret scanner for tracked and staged MeasuredField sources.

Purpose: reject accidental key material before commits/CI. No outbound traffic,
no value printing, no live credential validation. This is defense-in-depth and
does not replace dedicated server-side/remote history scanning.

Examples:
    python -B scripts/secret_scan.py --tracked
    python -B scripts/secret_scan.py --staged
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".json", ".jsonc", ".yaml", ".yml", ".toml", ".xml",
    ".md", ".rst", ".txt", ".html", ".css", ".scss", ".sql",
    ".sh", ".bash", ".cmd", ".bat", ".ps1", ".ini", ".cfg",
    ".env", ".conf", ".properties",
}
MAX_BYTES = 3 * 1024 * 1024
# Exactly one audited, original AI-generated MP4 is allowed above the normal
# source-scan threshold. Every future change requires a new reviewed SHA-256.
# No wildcard video bypass, directory exception, or adjustable size allowance.
PINNED_LARGE_MEDIA = {
    "app/static/media/measuredfield-business-launch-film-portrait.mp4": (
        "6fda494826d3bec8552dcb71458805585c5a1817c635b6777e3ef1f9003dd7fb"
    ),
}
MAX_PINNED_MEDIA_BYTES = 12 * 1024 * 1024
BLOCKED_BASE_NAMES = {
    "credentials.json", "token.json", "service-account.json",
    "service_account.json", "secrets.json", "secret.json",
    ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "id_ecdsa",
}
BLOCKED_SUFFIXES = {".pem", ".p12", ".pfx", ".keystore", ".key"}
# String fragments are concatenated so the scanner does not match its own
# source code when the repository is being checked.
PATTERNS = [
    ("private_key_header", re.compile(
        re.escape("-----BEGIN ") + r"(?:RSA |EC |OPENSSH |DSA )?" +
        re.escape("PRIVATE " + "KEY-----")
    )),
    ("openai_api_key", re.compile(re.escape("sk-" + "proj-") + r"[A-Za-z0-9_-]{20,}")),
    ("anthropic_api_key", re.compile(re.escape("sk-" + "ant-api") + r"[A-Za-z0-9_-]{18,}")),
    ("github_classic_token", re.compile(re.escape("gh" + "p_") + r"[A-Za-z0-9]{30,}")),
    ("github_fine_grained_token", re.compile(re.escape("github_" + "pat_") + r"[A-Za-z0-9_]{30,}")),
    ("stripe_live_secret", re.compile(re.escape("sk_" + "live_") + r"[A-Za-z0-9]{18,}")),
    ("stripe_live_restricted", re.compile(re.escape("rk_" + "live_") + r"[A-Za-z0-9]{18,}")),
    ("stripe_signing_secret", re.compile(re.escape("wh" + "sec_") + r"[A-Za-z0-9]{18,}")),
    ("google_api_key", re.compile(re.escape("AI" + "za") + r"[A-Za-z0-9_-]{30,}")),
    ("aws_access_key", re.compile(r"\b(?:AK" + "IA|AS" + r"IA)[A-Z0-9]{16}\b")),
    ("slack_token", re.compile(re.escape("xo" + "xb-") + r"[0-9]{8,}-[A-Za-z0-9-]{20,}")),
    # Warn about a specific basic-auth credential in a URL, not all fake test URLs.
    ("credential_in_url", re.compile(
        rb"(?:postgres(?:ql)?|mysql|redis|https?)://[A-Za-z0-9_.-]+:"
        rb"(?!(?:test|fake|dummy|synthetic|example|redacted|changeme))"
        rb"[^@\s]{12,}@", re.IGNORECASE)),
]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def _blocked_path(name: str) -> str | None:
    path = Path(name.replace("\\", "/"))
    base = path.name.lower()
    if base in BLOCKED_BASE_NAMES:
        return "prohibited_sensitive_filename"
    if base == ".env" or (base.startswith(".env.") and base not in
                          {".env.example", ".env.sample", ".env.template"}):
        return "tracked_runtime_environment"
    if path.suffix.lower() in BLOCKED_SUFFIXES:
        return "prohibited_private_key_file"
    if base.endswith((".credentials.json", ".secrets.json", ".private.json")):
        return "prohibited_sensitive_filename"
    return None


def _safe_relative(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return (
        bool(normalized)
        and not normalized.startswith("/")
        and ":" not in normalized.split("/")[0]
        and all(part not in ("", ".", "..") for part in normalized.split("/"))
        and not any(ord(ch) < 32 or ord(ch) == 127 for ch in normalized)
    )


def scan_bytes(path: str, raw: bytes) -> list[Finding]:
    """Return only location and category. Never return/print credential values."""
    if not _safe_relative(path):
        return [Finding("<invalid-path>", 0, "unsafe_path")]
    blocked = _blocked_path(path)
    if blocked:
        return [Finding(path, 0, blocked)]
    if len(raw) > MAX_BYTES:
        pinned = PINNED_LARGE_MEDIA.get(path)
        if (pinned is None or len(raw) > MAX_PINNED_MEDIA_BYTES
                or len(raw) < 12 or raw[4:8] != b"ftyp"
                or hashlib.sha256(raw).hexdigest() != pinned):
            return [Finding(path, 0, "oversized_unscanned_file")]
        # Byte-for-byte validated against the independently archived media.
        # This MP4 cannot be replaced or extended without failing the scan.
        return []
    # Scan binary as bytes as well: recognizable credentials can be embedded in
    # non-text assets. Malformed UTF-8 is handled without crashing.
    issues: list[Finding] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        for rule, pattern in PATTERNS:
            # Some patterns use bytes because they apply only to byte strings.
            content = line if isinstance(pattern.pattern, bytes) else line.decode(
                "utf-8", errors="replace")
            if pattern.search(content):
                issues.append(Finding(path, line_number, rule))
    return issues


def _git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, check=False,
    )
    if proc.returncode:
        raise RuntimeError("Git query failed; refusing an incomplete security scan")
    return proc.stdout


def get_paths(root: Path, mode: str) -> list[str]:
    if mode == "tracked":
        raw = _git(root, "ls-files", "-z", "--cached")
    elif mode == "staged":
        raw = _git(root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT")
    else:
        raise ValueError("Unknown scanner mode")
    return sorted(set(path.decode("utf-8", "surrogateescape")
                      for path in raw.split(b"\x00") if path))


def scan_repository(root: Path, mode: str) -> tuple[list[Finding], int]:
    repo = root.resolve(strict=True)
    # If the caller passes a subdirectory, git ls-files returns paths relative
    # to that subdirectory. Refuse it rather than silently missing files.
    _git(repo, "rev-parse", "--show-toplevel")
    if _git(repo, "rev-parse", "--show-prefix").strip():
        raise RuntimeError("Secret scan must run from the Git repository root")
    paths = get_paths(repo, mode)
    issues: list[Finding] = []
    for name in paths:
        if not _safe_relative(name):
            issues.append(Finding("<invalid-path>", 0, "unsafe_git_path"))
            continue
        path = repo / name
        if mode == "staged":
            blob = _git(repo, "show", ":" + name)
        else:
            # Reject symlinks instead of following them to local user secrets.
            if path.is_symlink():
                issues.append(Finding(name, 0, "tracked_symbolic_link"))
                continue
            try:
                blob = path.read_bytes()
            except OSError:
                issues.append(Finding(name, 0, "unreadable_tracked_file"))
                continue
        issues.extend(scan_bytes(name, blob))
    return issues, len(paths)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed, redacted repository secret scan")
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument("--tracked", action="store_true", help="Scan all tracked working-tree files")
    choices.add_argument("--staged", action="store_true", help="Scan all changed staged Git blobs")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    mode = "staged" if args.staged else "tracked"
    try:
        issues, files = scan_repository(args.root, mode)
    except (OSError, ValueError, RuntimeError) as error:
        print("SECRET_SCAN_ERROR: incomplete scan (" + type(error).__name__ + ")",
              file=sys.stderr)
        return 2
    for issue in issues[:100]:
        print("SECRET_SCAN_FINDING", issue)
    if len(issues) > 100:
        print("SECRET_SCAN_FINDING", len(issues) - 100, "more findings (not displayed)")
    if issues:
        print(f"SECRET_SCAN_FAIL: {len(issues)} findings in {files} files ({mode})")
        return 1
    print(f"SECRET_SCAN_PASS: scanned {files} Git files ({mode}), no matching secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
