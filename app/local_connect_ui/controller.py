"""Owner-side application logic; deliberately NOT exposed through MCP tools.

This module is imported by the local Windows window, not by the AI-facing server.
All file edits are staged by the connector and require local review/confirmation.
"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

from app.local_connect import AccessDenied, ConflictError, LocalConnect


class OwnerReviewRequired(AccessDenied):
    """Local owner did not explicitly authorize this consequential action."""


@dataclass(frozen=True)
class ChangeReview:
    proposal_id: str
    project_name: str
    relative_path: str
    old_sha256: str | None
    new_sha256: str
    file_existed: bool
    displayed_diff: str
    review_token: str


def safe_display(value: str) -> str:
    """Expose rather than execute Unicode control/terminal and bidi characters."""
    return "".join(
        char if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"}
        else ("\\u%04x" % ord(char) if ord(char) <= 0xFFFF
              else "\\U%08x" % ord(char))
        for char in value
    )


class OwnerWorkspaceController:
    MAX_DIFF_CHARS = 30000
    MAX_DIFF_LINES = 400

    def __init__(self, store: LocalConnect | None = None) -> None:
        self.store = store or LocalConnect()

    def projects(self) -> list[dict]:
        """Owner-local interface may show real roots. MCP list never does."""
        return self.store.grants(include_roots=True)

    def connect(self, name: str, path: str, allow_proposals: bool = False,
                confirmed_write: bool = False) -> dict:
        if allow_proposals and not confirmed_write:
            raise OwnerReviewRequired("You must confirm permission to propose edits")
        return self.store.grant(name, path, "readwrite" if allow_proposals else "read")

    def disconnect(self, project_id: str, confirmed: bool = False) -> bool:
        if not confirmed:
            raise OwnerReviewRequired("Confirm before disconnecting a folder")
        return self.store.revoke(project_id)

    def proposed_changes(self) -> list[dict]:
        return self.store.pending()

    def review_change(self, proposal_id: str) -> ChangeReview:
        proposal = next(
            (p for p in self.store.pending() if p["proposal_id"] == proposal_id), None
        )
        if not proposal:
            raise AccessDenied("This proposal is no longer pending")
        diff = self.store.diff(proposal_id)
        if len(diff) > self.MAX_DIFF_CHARS or len(diff.splitlines()) > self.MAX_DIFF_LINES:
            raise AccessDenied("The proposed change is too large for this review window")
        safe = safe_display(diff)
        token = hashlib.sha256(
            (proposal_id + "\0" + diff).encode("utf-8")
        ).hexdigest()
        return ChangeReview(
            proposal_id=proposal_id,
            project_name=proposal["project"],
            relative_path=proposal["path"],
            old_sha256=proposal["old_sha256"],
            new_sha256=proposal["new_sha256"],
            file_existed=bool(proposal["file_existed"]),
            displayed_diff=safe or "(No text difference)",
            review_token=token,
        )

    def approve_change(self, proposal_id: str, review_token: str,
                       confirmed: bool = False) -> dict:
        if not confirmed:
            raise OwnerReviewRequired("You must explicitly approve the reviewed change")
        current_review = self.review_change(proposal_id)
        if not review_token or review_token != current_review.review_token:
            raise ConflictError("The change review is stale; inspect it again")
        # Store verifies the original hash, active grant, target and content again.
        return self.store.approve(proposal_id)

    def reject_change(self, proposal_id: str, confirmed: bool = False) -> bool:
        if not confirmed:
            raise OwnerReviewRequired("Confirm before rejecting a proposal")
        return self.store.reject(proposal_id)

    def activity(self, limit: int = 40) -> list[dict]:
        return self.store.audit(limit)
