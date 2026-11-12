"""Safe project-specific check entrypoint for MeasuredField Antigravity runs.

Resolves the project root from this file rather than assuming CLI shell cwd.
Does not deploy, install packages, access credentials, or modify outside this tree.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if ROOT.name != "mf_platform" or not (ROOT / "app" / "main.py").is_file():
    raise SystemExit("Refusing to run outside mf_platform")

sources = sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "tests").rglob("test_*.py"))
for source in sources:
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
print(f"Syntax checked: {len(sources)} Python files", flush=True)

env = os.environ.copy()
env["PYTHONDONTWRITEBYTECODE"] = "1"
command = [sys.executable, "-B", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
print("Running focused project suite from:", ROOT, flush=True)
raise SystemExit(subprocess.run(command, cwd=ROOT, env=env, check=False).returncode)
