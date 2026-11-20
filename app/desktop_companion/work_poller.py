"""Owner-started outbound HTTPS worker for actual computer Work-mode file actions.

Never accepts inbound network connections. Requires BOTH a verified current
MeasuredField Work-mode device and the owner's local time-limited write pairing.
The only model-origin actions implemented are file write and folder creation.
"""
from __future__ import annotations

import argparse
import hashlib
import time
from types import SimpleNamespace

from app.account_link.client import DeviceApi, DeviceClientError, DeviceRevoked
from app.account_link.runtime_guard import AccountRuntimeGuard
from app.local_connect.store import AccessDenied, ConflictError
from app.paired_connect.registry import BoundSession, PairingRegistry


class _VerifiedWorkerAccount:
    """Non-UI account verifier that preserves a freshly owner-approved pairing.

    AccountLinkController is an interactive UI lifecycle controller and intentionally
    revokes old pairing sessions on app startup. A worker launched by that very
    UI must NOT instantiate it: doing so revokes the new Work pairing. Instead,
    check live server authority through the same fail-closed AccountRuntimeGuard
    used on every BoundSession file operation.
    """

    def __init__(self, home):
        self.guard = AccountRuntimeGuard(home)
        self.secrets = self.guard.secrets

    def refresh(self):
        self.guard.check(action="write")
        state = self.guard._read_account()
        if state is None or state.get("status") != "active" or state.get("access") != "work":
            raise AccessDenied("The linked computer is not verified for Work mode")
        return SimpleNamespace(
            status=state["status"], access=state["access"],
            server_url=state["server_url"], device_id=state["device_id"],
        )


class WorkPoller:
    def __init__(self, *, pairing_id: str, home=None, api_factory=DeviceApi,
                 account=None, registry=None):
        self.registry = registry or PairingRegistry(home)
        self.account = account or _VerifiedWorkerAccount(self.registry.home)
        self.pairing_id = pairing_id
        self.api_factory = api_factory

    def _execute(self, job: dict) -> str:
        action, payload = job["action"], job["payload"]
        if not isinstance(payload, dict) or set(payload) != {
                "project_id", "relative_path", "content", "expected_sha256"}:
            raise AccessDenied("Unrecognized work payload")
        project_id, path = payload["project_id"], payload["relative_path"]
        session = BoundSession(
            self.pairing_id,
            self.registry.token_for_local_launcher(self.pairing_id),
            self.registry.home,
        )
        if action == "write_file":
            content = payload["content"]
            if not isinstance(content, str):
                raise AccessDenied("Invalid file content")
            expected_content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            # A lease can expire after local execution but before cloud receipt.
            # A retry then verifies the persisted bytes and does not overwrite.
            try:
                existing = session.read(project_id, path)
            except AccessDenied:
                existing = None
            if existing and existing["sha256"] == expected_content_hash:
                return expected_content_hash
            result = session.write_file(
                project_id, path, content, payload["expected_sha256"])
            return result["sha256"]
        if action == "make_directory":
            if payload["content"] is not None or payload["expected_sha256"] is not None:
                raise AccessDenied("Invalid directory payload")
            try:
                # A retried identical directory operation is successful when the
                # intended folder already exists and remains owner-accessible.
                session.browse(project_id, path)
            except AccessDenied:
                session.make_directory(project_id, path)
            return hashlib.sha256(("directory:" + path).encode("utf-8")).hexdigest()
        raise AccessDenied("Unsupported computer work action")

    def once(self) -> bool:
        """Claim, execute, and acknowledge one job; false means no queued work."""
        # Stop immediately when the owner's local Work session is revoked or
        # expires, even if no remote jobs are pending. No network poll follows.
        approved = self.registry.token_for_local_launcher(self.pairing_id)
        local_scope = self.registry.authorize(self.pairing_id, approved)
        if not local_scope.get("can_write"):
            raise AccessDenied("This local connection cannot make file changes")
        view = self.account.refresh()
        if view.status != "active" or view.access != "work":
            raise AccessDenied("Computer is not owner-verified for Work mode")
        credential = self.account.secrets.load()
        if not credential:
            raise AccessDenied("Computer device credential is unavailable")
        # The owner chooses project folders in this local companion, never in
        # ChatGPT. Upload names/opaque IDs only during an active Work pairing.
        allowed_ids = set(local_scope["grants"])
        projects = [
            {"project_id": item["id"], "name": item["name"],
             "access": "readwrite"}
            for item in self.registry.connect.grants()
            if item["id"] in allowed_ids and item["access"] == "readwrite"
        ]
        if not projects:
            raise AccessDenied("This session has no approved writable project")
        api = self.api_factory(view.server_url)
        try:
            # Refuse to fetch remote work if the current local grants could not
            # be synchronized. Server roster entries expire when polling stops.
            api.report_work_projects(credential, projects)
            job = api.claim_work(credential)
            if job is None:
                return False
            try:
                result = self._execute(job)
            except (AccessDenied, ConflictError, ValueError):
                api.finish_work(
                    credential, job["job_id"], job["lease_token"],
                    succeeded=False, error_code="permission_or_conflict")
                return True
            except OSError:
                api.finish_work(
                    credential, job["job_id"], job["lease_token"],
                    succeeded=False, error_code="file_system_error")
                return True
            api.finish_work(
                credential, job["job_id"], job["lease_token"],
                succeeded=True, digest=result)
            return True
        finally:
            api.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MeasuredField owner-authorized desktop computer worker")
    parser.add_argument("--pairing-id", required=True,
                        help="Active temporary local Work-mode pairing ID")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=7)
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 60:
        parser.error("Poll interval must be between 5 and 60 seconds")
    poller = WorkPoller(pairing_id=args.pairing_id)
    try:
        while True:
            try:
                processed = poller.once()
            except DeviceRevoked:
                # The cloud explicitly rejected the device bearer. Do not
                # keep polling with credentials the owner has revoked.
                print("Computer disconnected; the work session stopped.", flush=True)
                return 2
            except DeviceClientError:
                # A leased job may have saved a real file even when the
                # receipt could not reach the server. The next claim retries
                # under the same immutable job id and on-disk SHA check.
                print("Workspace temporarily unavailable. No work completion is claimed.", flush=True)
                if args.once:
                    return 2
                time.sleep(args.poll_seconds)
                continue
            print("Work request acknowledged." if processed else "No work waiting.", flush=True)
            if args.once:
                return 0
            time.sleep(args.poll_seconds)
    except (AccessDenied, OSError):
        print("Work session stopped; verify your computer connection and pairing.")
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
