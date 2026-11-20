"""MF-004: tenant-isolated owner-approved inquiry replies.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_replies",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("to_email", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("last_error", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["org_id", "lead_id"], ["leads.org_id", "leads.id"],
            ondelete="CASCADE", name="fk_lead_reply_same_org"
        ),
        sa.CheckConstraint(
            "status IN ('draft','queued','accepted','failed','not_configured')",
            name="ck_lead_reply_status"
        ),
        sa.CheckConstraint("char_length(subject) BETWEEN 1 AND 200", name="ck_lead_reply_subject"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 4000", name="ck_lead_reply_body"),
        sa.CheckConstraint(
            "(status = 'draft' AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status <> 'draft' AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_lead_reply_explicit_approval"
        ),
    )
    op.create_index("ix_lead_replies_org_id", "lead_replies", ["org_id"])
    op.create_index("ix_lead_replies_org_lead_date", "lead_replies", ["org_id", "lead_id", "created_at"])
    op.execute("ALTER TABLE lead_replies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE lead_replies FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON lead_replies "
               "USING (org_id = app_org()) WITH CHECK (org_id = app_org())")


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM lead_replies) THEN
            RAISE EXCEPTION 'Cannot downgrade: owner reply history must be preserved';
          END IF;
        END $$;
    """)
    op.drop_index("ix_lead_replies_org_lead_date", table_name="lead_replies")
    op.drop_index("ix_lead_replies_org_id", table_name="lead_replies")
    op.drop_table("lead_replies")
