"""MF-064: source-linked, reviewable takeoff measurement identities."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
import re

RightToProcess = Literal["synthetic", "owner_authorized", "licensed"]
ReviewStatus = Literal["review_required", "human_verified"]

@dataclass(frozen=True)
class DrawingRef:
    source_sha256: str
    sheet_id: str
    revision_id: str
    page_index: int
    viewport_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("A lowercase SHA-256 source hash is required")
        for item in (self.sheet_id, self.revision_id, self.viewport_id):
            if not isinstance(item, str) or not 1 <= len(item.strip()) <= 128:
                raise ValueError("Sheet, revision, and viewport identifiers are required")
        if type(self.page_index) is not int or self.page_index < 0:
            raise ValueError("Page index must be a nonnegative integer")

    @property
    def key(self) -> str:
        return (f"{self.source_sha256}:{self.sheet_id}@{self.revision_id}:"
                f"p{self.page_index}/{self.viewport_id}")

@dataclass(frozen=True)
class PlanPermission:
    rights_basis: RightToProcess
    approved_by: str
    approval_reference: str

    def __post_init__(self) -> None:
        if self.rights_basis not in ("synthetic", "owner_authorized", "licensed"):
            raise ValueError("An explicit rights basis is required")
        if not isinstance(self.approved_by, str) or not self.approved_by.strip():
            raise ValueError("Processing requires an identified authorized approver")
        if not isinstance(self.approval_reference, str) or not self.approval_reference.strip():
            raise ValueError("Processing requires a consent/license evidence reference")

@dataclass(frozen=True)
class MeasurementReview:
    status: ReviewStatus = "review_required"
    reviewer_id: str | None = None
    approval_reference: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("review_required", "human_verified"):
            raise ValueError("Unknown review status")
        if self.status == "human_verified":
            if not self.reviewer_id or not self.approval_reference:
                raise ValueError("Verified measurements need reviewer and audit evidence")
        elif self.reviewer_id or self.approval_reference:
            raise ValueError("Unverified measurements cannot carry approval metadata")
