"""Local owner interface to approve exactly which folders and edits AI can access."""
from __future__ import annotations

import argparse
import json
import sys

from .store import AccessDenied, ConflictError, LocalConnect


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="measuredfield-connect",
        description="MeasuredField Connected Workspace: local owner grants and edit approvals.",
    )
    sub = p.add_subparsers(dest="action", required=True)
    grant = sub.add_parser("grant", help="Allow access to ONE existing project folder")
    grant.add_argument("--path", required=True, help="Absolute path to existing project folder")
    grant.add_argument("--name", required=True, help="Friendly project name")
    grant.add_argument("--access", choices=("read", "readwrite"), default="read")
    sub.add_parser("grants", help="List active grants (including their local roots)")
    revoke = sub.add_parser("revoke", help="Disconnect a folder and invalidate pending changes")
    revoke.add_argument("--id", required=True)
    sub.add_parser("pending", help="Review pending edits by ID and filename")
    approve = sub.add_parser("approve", help="Show diff and require human confirmation to apply")
    approve.add_argument("--id", required=True)
    reject = sub.add_parser("reject", help="Decline a proposed edit")
    reject.add_argument("--id", required=True)
    audit = sub.add_parser("audit", help="Show action metadata without file content")
    audit.add_argument("--limit", type=int, default=30)
    sub.add_parser("server", help="Start private stdio MCP server")
    return p


def _out(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "server":
            from .mcp_server import main as serve
            serve()
            return 0
        local = LocalConnect()
        if args.action == "grant":
            print("Granting access only to the selected project. No other folder is shared.")
            if args.access == "readwrite":
                response = input("Type GRANT WRITE to enable edit proposals for this folder: ").strip()
                if response != "GRANT WRITE":
                    print("Write grant canceled.")
                    return 2
            _out(local.grant(args.name, args.path, args.access))
        elif args.action == "grants":
            _out(local.grants(include_roots=True))
        elif args.action == "revoke":
            if not local.revoke(args.id):
                print("No active grant with that ID.")
                return 1
            print("Permission revoked. Pending changes for this grant are invalidated.")
        elif args.action == "pending":
            _out(local.pending())
        elif args.action == "approve":
            matches = [x for x in local.pending() if x["proposal_id"] == args.id]
            if not matches:
                raise AccessDenied("Change is not pending or its project is disconnected")
            _out(matches[0])
            change = local.diff(args.id)
            if len(change) > 30_000 or len(change.splitlines()) > 400:
                print("Diff is too large for safe CLI review; approval refused.")
                return 2
            print("--- REVIEW THIS CHANGE BEFORE APPROVAL ---")
            print(change if change else "(no text difference)")
            if input("Type APPROVE " + args.id + " to apply this edit: ").strip() != "APPROVE " + args.id:
                print("No change applied.")
                return 2
            _out(local.approve(args.id))
        elif args.action == "reject":
            _out({"rejected": local.reject(args.id)})
        elif args.action == "audit":
            _out(local.audit(args.limit))
        return 0
    except (AccessDenied, ConflictError, OSError, ValueError) as exc:
        print("Action denied: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
