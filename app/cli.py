"""Operational commands. Run as:  python -m app.cli <command>

  create-operator <email>   Create (or promote) a platform operator. The password is read
                            from the MF_OPERATOR_PASSWORD environment variable so it never
                            appears in shell history or process arguments.
  isolation-check           Exit non-zero if the database cannot isolate tenants.
  run-jobs                  Run queued jobs once (for single-service deployments or cron).
  purge                     Retention sweep: expired sessions, tokens, rate-limit windows.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import select

from .db import isolation_self_check, session_scope
from .models import User
from .security import hash_password, normalize_email, password_problem, valid_email
from .services import jobs, tenant_data


def create_operator(email: str) -> int:
    email = normalize_email(email)
    password = os.environ.get("MF_OPERATOR_PASSWORD", "")
    if not valid_email(email):
        print("Enter a valid email address.", file=sys.stderr)
        return 2
    problem = password_problem(password)
    if problem:
        print("MF_OPERATOR_PASSWORD: " + problem, file=sys.stderr)
        return 2
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, name="Operator")
            db.add(user)
        user.password_hash = hash_password(password)
        user.is_platform_admin = True
    print("Operator ready: " + email)
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "create-operator" and len(argv) == 3:
        return create_operator(argv[2])
    if command == "isolation-check":
        problems = isolation_self_check()
        for problem in problems:
            print("FAIL: " + problem, file=sys.stderr)
        print("Tenant isolation checks passed." if not problems else f"{len(problems)} problem(s).")
        return 1 if problems else 0
    if command == "run-jobs":
        print(f"Ran {jobs.run_due(200)} job(s).")
        return 0
    if command == "purge":
        with session_scope() as db:
            print(tenant_data.purge_expired(db))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
