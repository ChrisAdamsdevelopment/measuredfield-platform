"""Append-only platform-operator security events (MF-005).

Revision ID: 0007
Revises: 0006

No tenant table is modified. Existing populated databases retain prior rows.
No free-form metadata; immutable history via database trigger.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_security_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=False),
        sa.Column("target_type", sa.String(24), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason_code", sa.String(40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "action IN ('order.mark_paid', 'order.provision', 'jobs.run', "
            "'domain.verify', 'review.decide', 'skill.rollback')",
            name="ck_platform_security_action"),
        sa.CheckConstraint("outcome IN ('approved', 'denied')",
                           name="ck_platform_security_outcome"),
        sa.CheckConstraint("(outcome = 'approved' AND reason_code = '') OR "
                           "(outcome = 'denied' AND reason_code <> '')",
                           name="ck_platform_security_outcome_reason"),
        sa.CheckConstraint(
            "target_type IN ('order', 'job_queue', 'domain', "
            "'lesson_review', 'shared_skill')",
            name="ck_platform_security_target_type"),
        sa.CheckConstraint(
            "reason_code IN ('', 'invalid_reference', 'not_found', "
            "'invalid_state', 'already_paid', 'not_provisionable', "
            "'invalid_decision', 'consent_withdrawn', "
            "'already_verified', 'no_active_version', 'operation_failed')",
            name="ck_platform_security_reason"),
    )
    op.create_index("ix_platform_security_created", "platform_security_events",
                    ["created_at"])
    op.create_index("ix_platform_security_actor_created", "platform_security_events",
                    ["actor_user_id", "created_at"])
    op.execute("""
        CREATE FUNCTION reject_platform_security_history_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'platform security audit entries are immutable';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER platform_security_events_immutable
        BEFORE UPDATE OR DELETE ON platform_security_events
        FOR EACH ROW EXECUTE FUNCTION reject_platform_security_history_change()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS platform_security_events_immutable "
               "ON platform_security_events")
    op.execute("DROP FUNCTION IF EXISTS reject_platform_security_history_change()")
    op.drop_index("ix_platform_security_actor_created",
                  table_name="platform_security_events")
    op.drop_index("ix_platform_security_created",
                  table_name="platform_security_events")
    op.drop_table("platform_security_events")
