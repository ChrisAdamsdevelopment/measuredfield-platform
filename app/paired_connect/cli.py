"""Owner-only local pairing and immediate revoke controls."""
from __future__ import annotations

import argparse
import json
import sys

from app.local_connect import AccessDenied
from .registry import PairingRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measuredfield-pair",
        description="Local owner controls for temporary private project pairing.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("pair", help="Pair selected folder permissions with a local test client")
    p.add_argument("--name", required=True, help="Friendly name for trusted local client")
    p.add_argument("--grant-id", action="append", required=True, help="Approved local project ID")
    p.add_argument("--ttl-minutes", type=int, default=15)
    p.add_argument("--allow-proposals", action="store_true",
                   help="Optional, owner-approved suggestion permission (not automatic writes)")
    p.add_argument("--allow-writes", action="store_true",
                   help="Owner-approved direct project file changes, Work-mode device only")
    commands.add_parser("connections", help="List active, revoked and expired pairings")
    r = commands.add_parser("revoke", help="Immediately stop one paired connection")
    r.add_argument("--id", required=True)
    commands.add_parser("audit", help="Show metadata-only pairing events")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = PairingRegistry()
        if args.command == "pair":
            print("This is a PRIVATE LOCAL pilot, not a verified ChatGPT account connection.")
            print("It allows access only to the project IDs you select.")
            print("Project IDs: " + ", ".join(args.grant_id))
            print("Access: " + ("Work in selected projects" if args.allow_writes else
                                "Read + suggest edits" if args.allow_proposals else "Read only"))
            print("Expires after " + str(args.ttl_minutes) + " minutes.")
            if input("Type PAIR to approve this connection: ").strip() != "PAIR":
                return 2
            if args.allow_proposals:
                if input("Type ALLOW SUGGESTIONS to permit proposed edits: ").strip() != "ALLOW SUGGESTIONS":
                    return 2
            if args.allow_writes:
                from app.account_link.controller import AccountLinkController
                connected = AccountLinkController(registry.connect)
                connected.refresh()
                if not connected.can_work():
                    raise AccessDenied("Connect and verify this computer in Work mode before direct writes.")
                print("This permits actual file creation and replacement, not only suggestions.")
                print("Only owner-granted readwrite project roots are available; session expires.")
                if input("Type ALLOW DIRECT WRITES to authorize project work: ").strip() != "ALLOW DIRECT WRITES":
                    return 2
            print(json.dumps(registry.create(
                args.name, args.grant_id, owner_confirmed=True,
                allow_proposals=args.allow_proposals, allow_writes=args.allow_writes,
                owner_confirmed_write=args.allow_writes, expires_minutes=args.ttl_minutes
            ), indent=2))
            return 0
        if args.command == "connections":
            print(json.dumps(registry.list(), indent=2))
            return 0
        if args.command == "audit":
            print(json.dumps(registry.audit(), indent=2))
            return 0
        if args.command == "revoke":
            if input("Type DISCONNECT to revoke this pairing: ").strip() != "DISCONNECT":
                return 2
            if not registry.revoke(args.id):
                print("No active paired connection found.")
                return 1
            print("Connection revoked; future MCP calls will be denied.")
            return 0
    except (EOFError, KeyboardInterrupt, AccessDenied, OSError, ValueError) as exc:
        print("Action denied: " + str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
