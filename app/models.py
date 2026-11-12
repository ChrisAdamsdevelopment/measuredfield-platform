"""Database schema.

Two kinds of tables:

* PLATFORM tables hold MeasuredField's own records (accounts, prospects, orders, the
  job queue, the shared skill library). They are never scoped to a tenant.
* TENANT tables carry ``org_id`` and are protected by PostgreSQL row-level security
  (see migrations). ``TENANT_TABLES`` below is the single source of truth used by the
  migration, the RLS self-check at startup, export, deletion and the isolation tests.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, LargeBinary,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


PK = lambda: mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)  # noqa: E731
TS = lambda: mapped_column(DateTime(timezone=True), default=now, nullable=False)  # noqa: E731
OPT_TS = lambda: mapped_column(DateTime(timezone=True), nullable=True)  # noqa: E731


def org_fk():
    return mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
                         nullable=False, index=True)


# --------------------------------------------------------------------------- platform

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = PK()
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = TS()
    disabled_at: Mapped[datetime | None] = OPT_TS()


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = PK()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    active_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    csrf_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = TS()
    last_seen_at: Mapped[datetime] = TS()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = OPT_TS()


class AuthToken(Base):
    """Single-use invite and password-reset tokens. Only the SHA-256 is stored."""
    __tablename__ = "auth_tokens"
    id: Mapped[uuid.UUID] = PK()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # invite | reset
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = TS()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = OPT_TS()
    # A usable link exists only after it has been minted: at provider send time ("email")
    # or by the audited manual procedure ("manual"). Until then token_hash holds a random
    # value nobody has ever seen. issued_by records who ran a manual issue.
    issued_at: Mapped[datetime | None] = OPT_TS()
    issue_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    issued_via: Mapped[str | None] = mapped_column(String(20))
    issued_by: Mapped[str | None] = mapped_column(String(254))


class Assessment(Base):
    """A prospect's two-question discovery submission, before they are a customer."""
    __tablename__ = "assessments"
    id: Mapped[uuid.UUID] = PK()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    business_name: Mapped[str] = mapped_column(String(120), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    website: Mapped[str] = mapped_column(String(250), default="", nullable=False)
    industry: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    excited_text: Mapped[str] = mapped_column(Text, nullable=False)
    blocker_text: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    created_at: Mapped[datetime] = TS()


class ServiceOrder(Base):
    __tablename__ = "service_orders"
    id: Mapped[uuid.UUID] = PK()
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    offer_code: Mapped[str] = mapped_column(String(40), nullable=False)
    offer_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="usd", nullable=False)
    # approved_unpaid -> payment_pending -> paid -> provisioned ; consult_requested ; cancelled
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    approved_name: Mapped[str] = mapped_column(String(120), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(40), nullable=False)
    payment_provider: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(255), unique=True)
    payment_evidence: Mapped[str | None] = mapped_column(String(255))
    paid_at: Mapped[datetime | None] = OPT_TS()
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"))
    provisioned_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()


class PaymentEvent(Base):
    """Verified webhook deliveries, keyed by provider event id for idempotency."""
    __tablename__ = "payment_events"
    id: Mapped[uuid.UUID] = PK()
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = TS()
    __table_args__ = (UniqueConstraint("provider", "event_id"),)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = PK()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    run_after: Mapped[datetime] = TS()
    locked_at: Mapped[datetime | None] = OPT_TS()
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = TS()
    finished_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)


class EmailMessage(Base):
    """Outbox. Bodies never contain customer inquiry content, only links back to the app."""
    __tablename__ = "email_outbox"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    to_email: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # queued | sent | not_sent_no_provider | failed
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = TS()
    sent_at: Mapped[datetime | None] = OPT_TS()
    # Set for invitation and password-reset messages. Their stored body contains the
    # placeholder AUTH_LINK_PLACEHOLDER (app/services/email.py), never a usable link.
    auth_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_tokens.id", ondelete="SET NULL"), index=True)


class SiteHost(Base):
    """Hostname -> owning site. The only table consulted before a tenant context exists
    for public traffic, so it deliberately holds nothing private."""
    __tablename__ = "site_hosts"
    hostname: Mapped[str] = mapped_column(String(253), primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="subdomain", nullable=False)  # subdomain | custom
    verified_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()
    __table_args__ = (ForeignKeyConstraint(["site_id", "org_id"],
        ["sites.id", "sites.org_id"], name="fk_site_hosts_site_owner", ondelete="CASCADE"),)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class LessonReview(Base):
    """Operator review queue for candidate shared lessons. Holds ONLY sanitized text."""
    __tablename__ = "lesson_reviews"
    id: Mapped[uuid.UUID] = PK()
    source_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    sanitized_body: Mapped[str] = mapped_column(Text, nullable=False)
    checks: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|approved|rejected|withdrawn
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()


class SharedSkill(Base):
    __tablename__ = "shared_skills"
    id: Mapped[uuid.UUID] = PK()
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()


class SharedSkillVersion(Base):
    __tablename__ = "shared_skill_versions"
    id: Mapped[uuid.UUID] = PK()
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shared_skills.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # provenance: which review (and therefore which consenting org) this came from
    source_review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active|superseded|revoked
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()
    revoked_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (UniqueConstraint("skill_id", "number"),)


# ----------------------------------------------------------------------------- tenant

class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = PK()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    industry: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    plan: Mapped[str] = mapped_column(String(40), default="none", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active|deletion_pending|deleted
    learning_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    learning_opt_in_at: Mapped[datetime | None] = OPT_TS()
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = TS()


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # owner|admin|staff|readonly
    created_at: Mapped[datetime] = TS()
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("site_versions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()
    __table_args__ = (UniqueConstraint("id", "org_id", name="uq_sites_id_org_id"),)


class SiteVersion(Base):
    """Immutable rendered snapshot. Publishing points the site at one of these."""
    __tablename__ = "site_versions"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    html: Mapped[str] = mapped_column(Text, nullable=False)
    css: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (UniqueConstraint("site_id", "number"),)


class SiteEvent(Base):
    """Cookie-less daily aggregate counters from published sites. No visitor identifiers."""
    __tablename__ = "site_events"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # page_view | form_start | lead
    count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("site_id", "day", "kind"),)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    service: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    timing: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="website", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    follow_up_on: Mapped[date | None] = mapped_column(Date)
    first_response_at: Mapped[datetime | None] = OPT_TS()
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = TS()


class LeadNote(Base):
    __tablename__ = "lead_notes"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = TS()


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = TS()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)  # open|scheduled|in_progress|done|cancelled
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = TS()


class Proposal(Base):
    """Estimate/proposal sent to a customer, approved through a single-use link."""
    __tablename__ = "proposals"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    scope: Mapped[str] = mapped_column(Text, default="", nullable=False)
    items: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft|sent|approved|declined
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    decided_name: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = OPT_TS()
    decided_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = TS()


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()


class MemoryItem(Base):
    """Private business memory: observations and document chunks. Never leaves the tenant."""
    __tablename__ = "memory_items"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # observation | document_chunk
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()


class LessonCandidate(Base):
    __tablename__ = "lesson_candidates"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sanitized_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    checks: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # draft | blocked | submitted | approved | rejected | withdrawn
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    ownership_attested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()


class AssessmentReport(Base):
    """The prospect assessment, copied into the tenant when the organization is created."""
    __tablename__ = "assessment_reports"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    excited_text: Mapped[str] = mapped_column(Text, nullable=False)
    blocker_text: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = TS()


class Improvement(Base):
    """One concrete improvement, with an honest evidence status."""
    __tablename__ = "improvements"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    capability: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="assessment", nullable=False)
    # proposed | approved | implemented | dismissed
    status: Mapped[str] = mapped_column(String(20), default="proposed", nullable=False)
    metric: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # owner_stated | system_recorded | measured | unknown
    evidence_status: Mapped[str] = mapped_column(String(20), default="owner_stated", nullable=False)
    evidence_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    implemented_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = TS()
    __table_args__ = (Index("ix_audit_org_time", "org_id", "created_at"),)


class DataRequest(Base):
    __tablename__ = "data_requests"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # export | delete
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False)  # requested|cancelled|completed
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_after: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()


class DeletionReceipt(Base):
    """PLATFORM table: survives tenant deletion so there is proof of what was removed."""
    __tablename__ = "deletion_receipts"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    receipt: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = TS()


# Every table that carries org_id and must be covered by row-level security.
# Order matters for deletion (children before parents).
TENANT_TABLES = [
    "lead_notes", "leads", "proposals", "projects", "customers", "memory_items", "documents",
    "lesson_candidates", "assessment_reports", "improvements", "site_events", "site_versions",
    "sites", "data_requests", "audit_events", "memberships",
]
TENANT_MODELS = {
    "lead_notes": LeadNote, "leads": Lead, "proposals": Proposal, "projects": Project,
    "customers": Customer, "memory_items": MemoryItem, "documents": Document,
    "lesson_candidates": LessonCandidate, "assessment_reports": AssessmentReport,
    "improvements": Improvement, "site_events": SiteEvent, "site_versions": SiteVersion,
    "sites": Site, "data_requests": DataRequest, "audit_events": AuditEvent,
    "memberships": Membership,
}
