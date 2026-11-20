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
    CheckConstraint,
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


class McpIdentityLink(Base):
    """Explicitly verified external OAuth subject linked to a local account.

    Platform-level identity row, not tenant content. Enrollment MUST require a
    separate verified OIDC-code/PKCE owner session; no endpoint accepts sub/user_id
    supplied by a model or client as proof of ownership.
    """
    __tablename__ = "mcp_identity_links"
    id: Mapped[uuid.UUID] = PK()
    issuer: Mapped[str] = mapped_column(String(250), nullable=False)
    subject: Mapped[str] = mapped_column(String(250), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True)
    verified_method: Mapped[str] = mapped_column(String(24), nullable=False)
    linked_at: Mapped[datetime] = TS()
    revoked_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_mcp_identity_issuer_subject"),
        CheckConstraint("verified_method = 'oidc_pkce_owner'",
                        name="ck_mcp_identity_verified_method"),
    )


class McpOwnerOIDCChallenge(Base):
    """Ephemeral first-party OAuth account-link transaction.

    Never stores a raw state or nonce, a provider token, or a plaintext PKCE
    verifier. The pending verifier is encrypted under the app secret until the
    one-time callback consumes it.
    """
    __tablename__ = "mcp_owner_oidc_challenges"
    id: Mapped[uuid.UUID] = PK()
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issuer: Mapped[str] = mapped_column(String(250), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pkce_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = TS()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (
        CheckConstraint("length(state_hash) = 64", name="ck_oidc_link_state_hash_length"),
        CheckConstraint("length(nonce_hash) = 64", name="ck_oidc_link_nonce_hash_length"),
        Index("ix_oidc_link_expiry", "expires_at"),
    )


class BetaFeedback(Base):
    """PLATFORM support message intentionally submitted to MeasuredField.

    No org_id or customer data is copied automatically. The submitter sees
    that platform operators, not other business tenants, receive this message.
    """
    __tablename__ = "beta_feedback"
    id: Mapped[uuid.UUID] = PK()
    submitter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (
        CheckConstraint("kind IN ('bug', 'idea', 'other')", name="ck_beta_feedback_kind"),
        CheckConstraint("status IN ('new', 'reviewing', 'resolved')",
                        name="ck_beta_feedback_status"),
        CheckConstraint("char_length(message) BETWEEN 15 AND 1800",
                        name="ck_beta_feedback_message_length"),
        Index("ix_beta_feedback_status_created", "status", "created_at"),
    )


class PlatformSecurityEvent(Base):
    """PLATFORM-ONLY immutable operator audit. Contains no free-form metadata."""

    __tablename__ = "platform_security_events"
    id: Mapped[uuid.UUID] = PK()
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    created_at: Mapped[datetime] = TS()
    __table_args__ = (
        CheckConstraint("action IN ('order.mark_paid', 'order.provision', 'jobs.run', "
                        "'domain.verify', 'review.decide', 'skill.rollback')",
                        name="ck_platform_security_action"),
        CheckConstraint("outcome IN ('approved', 'denied')",
                        name="ck_platform_security_outcome"),
        CheckConstraint("(outcome = 'approved' AND reason_code = '') OR "
                        "(outcome = 'denied' AND reason_code <> '')",
                        name="ck_platform_security_outcome_reason"),
        CheckConstraint("target_type IN ('order', 'job_queue', 'domain', "
                        "'lesson_review', 'shared_skill')",
                        name="ck_platform_security_target_type"),
        CheckConstraint("reason_code IN ('', 'invalid_reference', 'not_found', "
                        "'invalid_state', 'already_paid', 'not_provisionable', "
                        "'invalid_decision', 'consent_withdrawn', "
                        "'already_verified', 'no_active_version', 'operation_failed')",
                        name="ck_platform_security_reason"),
        Index("ix_platform_security_created", "created_at"),
        Index("ix_platform_security_actor_created", "actor_user_id", "created_at"),
    )


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
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), index=True, nullable=True)
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
    settled_amount_cents: Mapped[int | None] = mapped_column(Integer)
    discount_amount_cents: Mapped[int | None] = mapped_column(Integer)
    tax_amount_cents: Mapped[int | None] = mapped_column(Integer)
    paid_at: Mapped[datetime | None] = OPT_TS()
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"))
    provisioned_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()


class PendingPaymentLinkClaim(Base):
    """Platform-only Stripe direct checkout awaiting verified owner election.

    No tenant resources are provisioned from a billing email. This row becomes
    claimable only through a signed event, verified Stripe API evidence and an
    authenticated owner with verified mailbox control.
    """
    __tablename__ = "payment_link_claims"
    id: Mapped[uuid.UUID] = PK()
    checkout_session_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    stripe_subscription_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    stripe_customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_payment_link_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_price_id: Mapped[str] = mapped_column(String(255), nullable=False)
    offer_code: Mapped[str] = mapped_column(String(40), nullable=False)
    billing_email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    amount_subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subscription_status: Mapped[str] = mapped_column(String(32), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending_claim", nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_orders.id", ondelete="SET NULL"), unique=True)
    claimed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime] = TS()
    claimed_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (
        CheckConstraint("status IN ('pending_claim', 'claimed', 'expired', 'needs_review')",
                        name="ck_payment_link_claim_status"),
        CheckConstraint("amount_subtotal_cents > 0 AND amount_total_cents >= 0 "
                        "AND discount_cents BETWEEN 0 AND amount_subtotal_cents "
                        "AND tax_cents >= 0 AND "
                        "amount_total_cents = amount_subtotal_cents - discount_cents + tax_cents",
                        name="ck_payment_link_claim_amounts"),
        Index("ix_payment_link_claims_status", "status"),
    )


class PaymentLinkClaimReview(Base):
    """Immutable platform review trail for disputed direct Stripe purchases.

    No tenant RLS: operators can inspect pending unassigned transactions, but
    must never see tenant content or grant access without a verified owner claim.
    """
    __tablename__ = "payment_link_claim_reviews"
    id: Mapped[uuid.UUID] = PK()
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_link_claims.id", ondelete="RESTRICT"),
        nullable=False, index=True)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = TS()
    __table_args__ = (
        CheckConstraint("action = 'recheck'", name="ck_payment_link_review_action"),
        CheckConstraint("outcome IN ('reopened', 'needs_review', 'unavailable')",
                        name="ck_payment_link_review_outcome"),
    )


class PaymentEvent(Base):
    """Verified webhook deliveries, keyed by provider event id for idempotency."""
    __tablename__ = "payment_events"
    id: Mapped[uuid.UUID] = PK()
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    received_at: Mapped[datetime] = TS()
    __table_args__ = (UniqueConstraint("provider", "event_id"),)


class BillingSubscription(Base):
    """Server-only Stripe billing facts. Never expose this platform table by org ID.

    Signed checkout establishes the order binding. Subscription and invoice webhook
    timestamps are tracked separately so reordered invoices cannot undo cancellations.
    Tenant access must use the linked organization and server-side RBAC.
    """
    __tablename__ = "billing_subscriptions"
    id: Mapped[uuid.UUID] = PK()
    stripe_subscription_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_orders.id", ondelete="SET NULL"), unique=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    invoice_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    latest_invoice_id: Mapped[str | None] = mapped_column(String(255))
    last_subscription_event_created: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_invoice_event_created: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    period_end: Mapped[datetime | None] = OPT_TS()
    grace_until: Mapped[datetime | None] = OPT_TS()
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    entitlement_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = TS()
    updated_at: Mapped[datetime] = TS()


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


class WebsiteWorkRequest(Base):
    """User-submitted website work for a separately authorized ChatGPT tool.

    Instructions are not an execution receipt. Completion requires a real
    version-checked draft mutation; publishing is a separate human action.
    """
    __tablename__ = "website_work_requests"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued", nullable=False)
    expected_draft_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_payload_sha256: Mapped[str | None] = mapped_column(String(64))
    resulting_draft_sha256: Mapped[str | None] = mapped_column(String(64))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = TS()
    completed_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (
        ForeignKeyConstraint(["site_id", "org_id"], ["sites.id", "sites.org_id"],
                             name="fk_website_work_owner_site", ondelete="CASCADE"),
        CheckConstraint("status IN ('queued', 'completed', 'cancelled')",
                        name="ck_website_work_status"),
        Index("ix_website_work_queue", "org_id", "status", "created_at"),
    )


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
    __table_args__ = (UniqueConstraint("org_id", "id", name="uq_leads_org_id"),)


class LeadNote(Base):
    __tablename__ = "lead_notes"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = TS()


class LeadReply(Base):
    """Tenant-scoped inquiry reply. Provider acceptance is NOT final delivery."""
    __tablename__ = "lead_replies"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    to_email: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", server_default="draft", nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = TS()
    approved_at: Mapped[datetime | None] = OPT_TS()
    accepted_at: Mapped[datetime | None] = OPT_TS()
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "lead_id"], ["leads.org_id", "leads.id"],
                             ondelete="CASCADE", name="fk_lead_reply_same_org"),
        CheckConstraint("status IN ('draft','queued','accepted','failed','not_configured')",
                        name="ck_lead_reply_status"),
        CheckConstraint("char_length(subject) BETWEEN 1 AND 200", name="ck_lead_reply_subject"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 4000", name="ck_lead_reply_body"),
        CheckConstraint(
            "(status = 'draft' AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status <> 'draft' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_lead_reply_explicit_approval"),
        Index("ix_lead_replies_org_lead_date", "org_id", "lead_id", "created_at"),
    )


class McpActionDraft(Base):
    """Tenant-scoped draft proposed through OAuth. NO external effect on creation.

    A separate MeasuredField owner session must approve or decline the draft.
    Models and MCP OAuth clients never receive approval or publishing authority.
    """
    __tablename__ = "mcp_action_drafts"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    oauth_client_id: Mapped[str] = mapped_column(String(250), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="inquiry_reply")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()
    __table_args__ = (
        UniqueConstraint("org_id", "created_by", "idempotency_key",
                         name="uq_mcp_draft_actor_idempotency"),
        ForeignKeyConstraint(["org_id", "lead_id"], ["leads.org_id", "leads.id"],
                             ondelete="CASCADE", name="fk_mcp_draft_lead_same_org"),
        CheckConstraint("kind = 'inquiry_reply'", name="ck_mcp_draft_kind"),
        CheckConstraint("status IN ('pending_review', 'approved', 'declined')",
                        name="ck_mcp_draft_status"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 4000",
                        name="ck_mcp_draft_body_length"),
        CheckConstraint("char_length(idempotency_key) BETWEEN 12 AND 80",
                        name="ck_mcp_draft_idempotency_length"),
        Index("ix_mcp_drafts_org_status_created", "org_id", "status", "created_at"),
    )


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


# ------------------------------------------------------------------ AI visibility lab
# Tenant-scoped, conservative provenance. No import can grant itself verified_live.

class VisibilityGscSnapshot(Base):
    __tablename__ = "visibility_gsc_snapshots"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="owner_csv", nullable=False)
    date_start: Mapped[date | None] = mapped_column(Date())
    date_end: Mapped[date | None] = mapped_column(Date())
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()
    __table_args__ = (
        UniqueConstraint("org_id", "digest", name="uq_vgs_org_digest"),
        UniqueConstraint("org_id", "id", name="uq_vgs_org_id"),
        Index("ix_vgs_org_created", "org_id", "created_at"),
    )


class VisibilityQuestion(Base):
    __tablename__ = "visibility_questions"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="Residential remodeling", nullable=False)
    market: Mapped[str] = mapped_column(String(90), default="Prescott, Arizona", nullable=False)
    origin: Mapped[str] = mapped_column(String(30), default="gsc_suggestion", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    source_queries: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()
    __table_args__ = (
        UniqueConstraint("org_id", "question_hash", name="uq_vq_org_hash"),
        UniqueConstraint("org_id", "id", name="uq_vq_org_id"),
        ForeignKeyConstraint(
            ["org_id", "source_snapshot_id"],
            ["visibility_gsc_snapshots.org_id", "visibility_gsc_snapshots.id"],
            name="fk_vq_snapshot_same_tenant"),
        Index("ix_vq_org_status", "org_id", "status"),
    )


class VisibilityExperiment(Base):
    __tablename__ = "visibility_experiments"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(22), default="planned", nullable=False)
    provider_requests_executed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_ve_org_id"),
        Index("ix_ve_org_created", "org_id", "created_at"),
    )


class VisibilityObservation(Base):
    __tablename__ = "visibility_observations"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    actual_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(90), default="", nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    grounding: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    reported_mentioned: Mapped[bool | None] = mapped_column(Boolean)
    observed_at: Mapped[datetime | None] = OPT_TS()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = TS()
    __table_args__ = (
        UniqueConstraint("org_id", "source_run_id", name="uq_vo_org_external"),
        ForeignKeyConstraint(
            ["org_id", "experiment_id"],
            ["visibility_experiments.org_id", "visibility_experiments.id"],
            name="fk_vo_experiment_same_tenant"),
        Index("ix_vo_org_experiment", "org_id", "experiment_id"),
        Index("ix_vo_org_provenance", "org_id", "provenance"),
    )

class VisibilityResearchBudget(Base):
    """Owner-approved tenant-local monthly reservation ceiling, in USD micro-units.

    A reservation is NOT a bill. Unknown outcomes remain reserved until reconciled.
    """
    __tablename__ = "visibility_research_budgets"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    period_month: Mapped[date] = mapped_column(Date(), nullable=False)
    ceiling_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    held_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reconciled_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    authorized_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    authorized_at: Mapped[datetime] = TS()
    __table_args__ = (
        UniqueConstraint("org_id", "period_month", name="uq_vrb_org_month"),
        UniqueConstraint("org_id", "id", name="uq_vrb_org_id"),
        CheckConstraint("ceiling_micros >= 0 AND ceiling_micros <= 500000000",
                        name="ck_vrb_ceiling"),
        CheckConstraint("held_micros >= 0 AND reconciled_micros >= 0",
                        name="ck_vrb_positive"),
        CheckConstraint("held_micros + reconciled_micros <= ceiling_micros",
                        name="ck_vrb_within_ceiling"),
    )


class VisibilityResearchReservation(Base):
    """Durable idempotent signed run reservation. No provider call in MF-036."""
    __tablename__ = "visibility_research_reservations"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    budget_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    planned_run_id: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    request_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    ceiling_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    approved_at: Mapped[datetime] = TS()
    cancelled_at: Mapped[datetime | None] = OPT_TS()
    dispatch_started_at: Mapped[datetime | None] = OPT_TS()
    provider_response_id: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_vrr_org_idempotency"),
        ForeignKeyConstraint(
            ["org_id", "budget_id"],
            ["visibility_research_budgets.org_id", "visibility_research_budgets.id"],
            ondelete="RESTRICT", name="fk_vrr_budget_same_tenant"),
        CheckConstraint("ceiling_micros > 0 AND ceiling_micros <= 100000000",
                        name="ck_vrr_ceiling"),
        CheckConstraint("status IN ('reserved','cancelled','dispatch_unknown','reconciled')",
                        name="ck_vrr_status"),
        Index("ix_vrr_org_status", "org_id", "status"),
        Index("ix_vrr_org_budget", "org_id", "budget_id"),
    )


class ConnectedDevice(Base):
    """A computer an owner connected to their business (MF-032). Tenant table.

    ``credential_hash`` is the SHA-256 of a random device credential that was returned to
    the computer once. ``status``: pending (redeemed a code, not yet confirmed by the owner),
    active, revoked, expired (never confirmed in time)."""
    __tablename__ = "connected_devices"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)             # chosen by the owner
    reported_name: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)  # claimed by the computer
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    access: Mapped[str] = mapped_column(String(10), nullable=False)           # read | suggest | work
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    check_number: Mapped[str] = mapped_column(String(6), default="", server_default="", nullable=False)
    confirm_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = TS()
    confirmed_at: Mapped[datetime | None] = OPT_TS()
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_seen_at: Mapped[datetime | None] = OPT_TS()
    credential_rotated_at: Mapped[datetime | None] = OPT_TS()
    revoked_at: Mapped[datetime | None] = OPT_TS()
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    revoked_reason: Mapped[str] = mapped_column(String(40), default="", server_default="", nullable=False)
    __table_args__ = (
        UniqueConstraint("credential_hash", name="uq_connected_devices_credential_hash"),
        UniqueConstraint("org_id", "id", name="uq_connected_devices_org_id_id"),
        CheckConstraint("access IN ('read', 'suggest', 'work')", name="ck_connected_devices_access"),
        CheckConstraint("status IN ('pending', 'active', 'revoked', 'expired')", name="ck_connected_devices_status"),
    )


class DeviceWorkProject(Base):
    """Short-lived, device-reported metadata for an owner-approved local grant.

    No filesystem paths, credentials or file contents ever leave the computer.
    This roster helps OAuth clients choose opaque local project identifiers;
    actual writes still require the live local pairing and directory checks.
    """
    __tablename__ = "device_work_projects"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    access: Mapped[str] = mapped_column(String(10), nullable=False)
    reported_at: Mapped[datetime] = TS()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "device_id"],
                             ["connected_devices.org_id", "connected_devices.id"],
                             name="fk_device_work_projects_device", ondelete="CASCADE"),
        UniqueConstraint("org_id", "device_id", "project_id",
                         name="uq_device_work_projects_identity"),
        CheckConstraint("access IN ('readwrite')",
                        name="ck_device_work_projects_access"),
        Index("ix_device_work_projects_fresh", "org_id", "device_id", "expires_at"),
    )


class DeviceWorkRequest(Base):
    """Tenant-isolated encrypted queued work for one owner-linked computer.

    A token-carrying local companion, not the OAuth caller, may lease/complete
    work for its own device. Payload never appears as plaintext in database.
    """
    __tablename__ = "device_work_requests"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    oauth_client_id: Mapped[str] = mapped_column(String(250), nullable=False)
    request_key: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    payload_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued", nullable=False)
    lease_hash: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = OPT_TS()
    result_hash: Mapped[str | None] = mapped_column(String(64))
    result_status: Mapped[str | None] = mapped_column(String(30))
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "device_id"],
                             ["connected_devices.org_id", "connected_devices.id"],
                             name="fk_device_work_owner_device", ondelete="CASCADE"),
        UniqueConstraint("org_id", "created_by", "request_key",
                         name="uq_device_work_request_idempotency"),
        CheckConstraint("action IN ('write_file', 'make_directory')", name="ck_device_work_action"),
        CheckConstraint("status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled', 'expired')",
                        name="ck_device_work_status"),
        Index("ix_device_work_claim", "org_id", "device_id", "status", "created_at"),
    )


class DeviceEvent(Base):
    """Activity summary reported by a connected computer. Metadata only: an action, a result,
    an owner-chosen project name and a count. Never file content or file paths."""
    __tablename__ = "device_events"
    id: Mapped[uuid.UUID] = PK()
    org_id: Mapped[uuid.UUID] = org_fk()
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("connected_devices.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    project_label: Mapped[str] = mapped_column(String(80), default="", server_default="", nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = TS()


class DevicePairingCode(Base):
    """PLATFORM table. A computer redeeming a code has no tenant yet, so this row is the one
    thing consulted before a tenant context exists. It holds a hash and ids, nothing private."""
    __tablename__ = "device_pairing_codes"
    id: Mapped[uuid.UUID] = PK()
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_name: Mapped[str] = mapped_column(String(80), nullable=False)
    access: Mapped[str] = mapped_column(String(10), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = OPT_TS()
    created_at: Mapped[datetime] = TS()
    __table_args__ = (UniqueConstraint("code_hash", name="uq_device_pairing_codes_code_hash"),)


# Every table that carries org_id and must be covered by row-level security.
# Order matters for deletion (children before parents).
TENANT_TABLES = [
    "visibility_research_reservations", "visibility_research_budgets",
    "website_work_requests", "device_work_requests", "device_work_projects", "device_events", "connected_devices",
    "visibility_observations", "visibility_experiments", "visibility_questions", "visibility_gsc_snapshots",
    "mcp_action_drafts", "lead_replies", "lead_notes", "leads", "proposals", "projects", "customers", "memory_items", "documents",
    "lesson_candidates", "assessment_reports", "improvements", "site_events", "site_versions",
    "sites", "data_requests", "audit_events", "memberships",
]
TENANT_MODELS = {
    "visibility_research_reservations": VisibilityResearchReservation,
    "visibility_research_budgets": VisibilityResearchBudget,
    "website_work_requests": WebsiteWorkRequest,
    "device_work_requests": DeviceWorkRequest,
    "device_work_projects": DeviceWorkProject,
    "device_events": DeviceEvent, "connected_devices": ConnectedDevice,
    "visibility_observations": VisibilityObservation,
    "visibility_experiments": VisibilityExperiment,
    "visibility_questions": VisibilityQuestion,
    "visibility_gsc_snapshots": VisibilityGscSnapshot,
    "mcp_action_drafts": McpActionDraft, "lead_replies": LeadReply, "lead_notes": LeadNote, "leads": Lead, "proposals": Proposal, "projects": Project,
    "customers": Customer, "memory_items": MemoryItem, "documents": Document,
    "lesson_candidates": LessonCandidate, "assessment_reports": AssessmentReport,
    "improvements": Improvement, "site_events": SiteEvent, "site_versions": SiteVersion,
    "sites": Site, "data_requests": DataRequest, "audit_events": AuditEvent,
    "memberships": Membership,
}
