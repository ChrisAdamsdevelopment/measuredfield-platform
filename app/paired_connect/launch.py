"""Start the paired private stdio MCP server, withholding secret from command args.

Used by a trusted local MCP client or (after separate approval) an outbound
private tunnel. Never start this as an unauthenticated public web service.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from app.local_connect import AccessDenied
from .registry import PairingRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch an owner-approved local paired MCP session")
    parser.add_argument("--pair-id", required=True, help="Connection ID; NOT a credential")
    args = parser.parse_args(argv)
    try:
        secret = PairingRegistry().token_for_local_launcher(args.pair_id)
    except (AccessDenied, OSError, ValueError):
        print("This connection is unavailable, expired or revoked.", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["MEASUREDFIELD_PAIR_ID"] = args.pair_id
    env["MEASUREDFIELD_PAIR_SECRET"] = secret
    # Do not log child environment, tokens or protocol messages.
    return subprocess.call(
        [sys.executable, "-m", "app.paired_connect.server"],
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr, env=env
    )


if __name__ == "__main__":
    raise SystemExit(main())
