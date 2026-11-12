"""Private business memory and the gated path to the shared skill library.

Adapted from the Shared Learning Hub (14_SHARED_LEARNING_HUB_20261111): the gate order,
the fail-closed defaults and the redaction rules (vendor/hub_privacy_filter.py) are the
Hub's. The storage is this platform's tenant-isolated PostgreSQL schema instead of the
Hub's SQLite file, and identity comes from the signed-in session instead of a tenant id
passed by the caller.

The path a lesson takes, every step explicit:

  private memory (tenant only)
    -> candidate lesson written by a member            [tenant]
    -> consent: organization opted in to sharing        [admin/owner decision]
    -> ownership attestation by the submitting member   [tenant]
    -> automatic sanitization + privacy checks          [fail closed]
    -> operator review queue (sanitized text only)      [platform]
    -> human approval -> new versioned shared skill     [platform operator]
    -> revocation on consent withdrawal or deletion     [automatic]

There is no automatic publication anywhere in this module.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select

from ..db import TenantSession
from ..models import (Customer, Document, LessonCandidate, LessonReview, Membership, MemoryItem,
                      Organization, SharedSkill, SharedSkillVersion, Site, User, now)
from ..vendor.forge_chunker import chunk_document
from ..vendor.hub_privacy_filter import (API_KEY_RE, PROPRIETARY_FORMULA_RE, assess_k_anonymity_risk,
                                         scan_and_redact_text)
from .audit import audit
from .tenant_data import revoke_org_contributions


class LearningError(ValueError):
    pass


def contains_secret(text: str) -> bool:
    """Credential check on the ORIGINAL text, before any redaction.

    The vendored Hub filter redacts phone numbers before API keys, so a key that contains
    a run of digits is partly rewritten as a phone number: the key prefix survives and the
    filter reports no secret. Checking the raw text first closes that gap without changing
    the provenance-tracked file. (Reported to the Hub's authors in INDEPENDENT_FINDINGS.md.)
    """
    return bool(API_KEY_RE.search(text or "") or PROPRIETARY_FORMULA_RE.search(text or ""))


# ------------------------------------------------------------------- private memory

def add_observation(db: TenantSession, org_id: uuid.UUID, user_id: uuid.UUID, content: str) -> MemoryItem:
    content = (content or "").strip()
    if not 5 <= len(content) <= 4000:
        raise LearningError("Write between 5 and 4000 characters.")
    item = MemoryItem(org_id=org_id, kind="observation", content=content, created_by=user_id)
    db.add(item)
    audit(db, org_id, user_id, "memory.observation_added", "memory_item", item.id)
    return item


def index_document(db: TenantSession, document: Document, text_content: str) -> int:
    """Chunk a text document into private memory with the Forge chunker. No embeddings,
    no model calls: this only prepares tenant-scoped retrieval units."""
    chunks = chunk_document(text_content[:200_000], chunk_size=800, overlap=80)[:200]
    for chunk in chunks:
        db.add(MemoryItem(org_id=document.org_id, kind="document_chunk",
                          source_document_id=document.id, content=chunk))
    return len(chunks)


def search_memory(db: TenantSession, query: str, limit: int = 20) -> list[MemoryItem]:
    """Keyword search over this tenant's memory only (RLS scopes the rows)."""
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", query or "")][:6]
    statement = select(MemoryItem).order_by(MemoryItem.created_at.desc()).limit(limit)
    for word in words:
        statement = statement.where(MemoryItem.content.ilike(f"%{word}%"))
    return list(db.scalars(statement))


# -------------------------------------------------------------------------- consent

def set_opt_in(db: TenantSession, org: Organization, user_id: uuid.UUID, opt_in: bool) -> None:
    if org.learning_opt_in == opt_in:
        return
    org.learning_opt_in = opt_in
    org.learning_opt_in_at = now() if opt_in else None
    audit(db, org.id, user_id, "learning.opt_in" if opt_in else "learning.opt_out", "organization", org.id)
    if not opt_in:
        revoked = revoke_org_contributions(db, org.id)
        for candidate in db.scalars(select(LessonCandidate).where(
                LessonCandidate.status.in_(("submitted", "approved")))):
            candidate.status = "withdrawn"
        audit(db, org.id, user_id, "learning.contributions_revoked", "organization", org.id,
              {"shared_versions_revoked": revoked})


# ----------------------------------------------------------------------- candidates

def _forbidden_terms(db: TenantSession, org: Organization) -> list[str]:
    """Names that would identify this tenant or its customers in shared text."""
    terms = {org.name}
    terms.update(n for n in db.scalars(select(Customer.name)) if n)
    terms.update(n for n in db.scalars(
        select(User.name).join(Membership, Membership.user_id == User.id).where(Membership.org_id == org.id)) if n)
    for site in db.scalars(select(Site)):
        terms.add(site.slug)
        terms.add(str(site.draft.get("name", "")))
        terms.add(str(site.draft.get("city", "")))
    expanded = set()
    for term in terms:
        term = term.strip()
        if len(term) >= 3:
            expanded.add(term)
    return sorted(expanded)


def create_candidate(db: TenantSession, org_id: uuid.UUID, user_id: uuid.UUID, title: str, body: str) -> LessonCandidate:
    title, body = (title or "").strip(), (body or "").strip()
    if not 5 <= len(title) <= 160 or not 40 <= len(body) <= 3000:
        raise LearningError("Give the lesson a title and 40 to 3000 characters describing the general method.")
    candidate = LessonCandidate(org_id=org_id, title=title, body=body, created_by=user_id)
    db.add(candidate)
    db.flush()
    audit(db, org_id, user_id, "learning.candidate_created", "lesson_candidate", candidate.id)
    return candidate


def submit_candidate(db: TenantSession, org: Organization, candidate: LessonCandidate,
                     user_id: uuid.UUID, *, attest_ownership: bool) -> LessonCandidate:
    """Run the consent, ownership and privacy gates. Any failure blocks the candidate."""
    if candidate.status not in ("draft", "blocked"):
        raise LearningError("This lesson has already been submitted.")
    if not org.learning_opt_in:
        raise LearningError("Your organization has not opted in to sharing general lessons.")
    if not attest_ownership:
        raise LearningError("Confirm that this method is yours to share.")

    terms = _forbidden_terms(db, org)
    city_terms = [str(s.draft.get("city", "")) for s in db.scalars(select(Site))]
    sanitized_title, title_findings = scan_and_redact_text(candidate.title, terms)
    sanitized_body, findings = scan_and_redact_text(candidate.body, terms)
    reconstruction = "LOW"
    for city in city_terms:
        if city and assess_k_anonymity_risk(candidate.title + " " + candidate.body, city) != "LOW":
            reconstruction = "HIGH_RECONSTRUCTION_RISK"

    # Second pass over the sanitized text: it must come back clean, and no tenant term
    # may survive in it (canary check).
    _, recheck = scan_and_redact_text(sanitized_title + "\n" + sanitized_body, terms)
    residual = [t for t in terms if t.lower() in (sanitized_title + " " + sanitized_body).lower()]
    reasons = []
    if findings["prompt_injections"] or title_findings["prompt_injections"]:
        reasons.append("Text contains instruction-like or script content.")
    if contains_secret(candidate.title + " " + candidate.body) or findings["secrets"] or title_findings["secrets"]:
        reasons.append("Text contains something that looks like a credential or proprietary formula.")
    if reconstruction != "LOW":
        reasons.append("Text is specific enough to identify the business or a customer.")
    if recheck["pii"] or recheck["secrets"] or residual:
        reasons.append("Personal or identifying details remained after redaction.")
    redactions = len(findings["pii"]) + len(findings["unique_identifiers"]) + len(title_findings["pii"]) \
        + len(title_findings["unique_identifiers"])

    candidate.sanitized_body = sanitized_body
    candidate.ownership_attested_by = user_id
    candidate.checks = {
        "consent": "organization_opted_in", "ownership": "member_attested_not_independently_verified",
        "redactions_applied": redactions, "reconstruction_risk": reconstruction,
        "automated_recheck": "clean" if not (recheck["pii"] or recheck["secrets"] or residual) else "failed",
        "blocked_reasons": reasons,
        "note": "Automated checks reduce risk; they are not proof of anonymity. A person reviews before publication.",
    }
    if reasons:
        candidate.status = "blocked"
        audit(db, org.id, user_id, "learning.candidate_blocked", "lesson_candidate", candidate.id,
              {"reasons": len(reasons)})
        return candidate

    candidate.status = "submitted"
    db.add(LessonReview(source_org_id=org.id, source_candidate_id=candidate.id, title=sanitized_title,
                        sanitized_body=sanitized_body, checks=candidate.checks))
    audit(db, org.id, user_id, "learning.candidate_submitted", "lesson_candidate", candidate.id,
          {"redactions": redactions})
    return candidate


# ------------------------------------------------------------- operator publication

def decide_review(db: TenantSession, review: LessonReview, reviewer: User, *, approve: bool,
                  skill_slug: str = "") -> SharedSkillVersion | None:
    """Human decision by a platform operator. Approval creates a new immutable version."""
    if not reviewer.is_platform_admin:
        raise LearningError("Only a platform operator can decide a review.")
    if review.status != "pending":
        raise LearningError("This review has already been decided.")
    review.reviewer_id, review.decided_at = reviewer.id, now()
    if not approve:
        review.status = "rejected"
        _mark_candidate(db, review, "rejected")
        return None
    # Consent is re-checked at the moment of publication, inside the source tenant.
    db.set_context(review.source_org_id, None)
    org = db.get(Organization, review.source_org_id)
    still_consenting = org is not None and org.learning_opt_in
    db.set_context(None, None)
    if not still_consenting:
        review.status = "withdrawn"
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", (skill_slug or review.title).lower()).strip("-")[:80] or "lesson"
    skill = db.scalar(select(SharedSkill).where(SharedSkill.slug == slug))
    if skill is None:
        skill = SharedSkill(slug=slug, title=review.title)
        db.add(skill)
        db.flush()
    number = (db.scalar(select(func.max(SharedSkillVersion.number)).where(SharedSkillVersion.skill_id == skill.id)) or 0) + 1
    if skill.active_version_id:
        previous = db.get(SharedSkillVersion, skill.active_version_id)
        if previous is not None and previous.status == "active":
            previous.status = "superseded"
    version = SharedSkillVersion(skill_id=skill.id, number=number, body=review.sanitized_body,
                                 source_review_id=review.id, source_org_id=review.source_org_id,
                                 approved_by=reviewer.id, is_synthetic=False)
    db.add(version)
    db.flush()
    skill.active_version_id = version.id
    review.status = "approved"
    _mark_candidate(db, review, "approved")
    return version


def _mark_candidate(db: TenantSession, review: LessonReview, status: str) -> None:
    db.set_context(review.source_org_id, None)
    candidate = db.get(LessonCandidate, review.source_candidate_id)
    if candidate is not None:
        candidate.status = status
        audit(db, review.source_org_id, None, "learning.candidate_" + status, "lesson_candidate", candidate.id)
    db.flush()
    db.set_context(None, None)


def rollback_skill(db: TenantSession, skill: SharedSkill, reviewer: User) -> SharedSkillVersion | None:
    """Revoke the active version and reactivate the newest earlier non-revoked one."""
    if not reviewer.is_platform_admin:
        raise LearningError("Only a platform operator can roll back a skill.")
    current = db.get(SharedSkillVersion, skill.active_version_id) if skill.active_version_id else None
    if current is None:
        return None
    current.status, current.revoked_at = "revoked", now()
    db.flush()
    fallback = db.scalar(select(SharedSkillVersion).where(
        SharedSkillVersion.skill_id == skill.id, SharedSkillVersion.status == "superseded")
        .order_by(SharedSkillVersion.number.desc()).limit(1))
    if fallback is not None:
        fallback.status = "active"
    skill.active_version_id = fallback.id if fallback else None
    return fallback


def active_skills(db: TenantSession) -> list[dict]:
    """What every tenant may read: active shared methods, with no source attribution."""
    rows = db.execute(
        select(SharedSkill.title, SharedSkill.slug, SharedSkillVersion.number, SharedSkillVersion.body,
               SharedSkillVersion.is_synthetic)
        .join(SharedSkillVersion, SharedSkillVersion.id == SharedSkill.active_version_id)
        .where(SharedSkillVersion.status == "active").order_by(SharedSkill.title)).all()
    return [{"title": r.title, "slug": r.slug, "version": r.number, "body": r.body,
             "synthetic": r.is_synthetic} for r in rows]
