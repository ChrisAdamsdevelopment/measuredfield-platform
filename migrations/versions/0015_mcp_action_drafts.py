"""MF-013: tenant-safe OAuth reply drafts, intentionally no customer email send.

Revision ID: 0015
Revises: 0014

Each draft is linked by a composite FK to an inquiry in its own business.
FORCE RLS applies to the application table owner and runtime roles. A draft
is inert pending an authenticated first-party owner approval.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_leads_org_id", "leads", ["org_id", "id"])
    op.create_table(
        "mcp_action_drafts",
        sa.Column("id", sa.UUID(), nullable=False, primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("lead_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False),
        sa.Column("oauth_client_id", sa.String(250), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reviewed_by", sa.UUID()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "created_by", "idempotency_key",
                            name="uq_mcp_draft_actor_idempotency"),
        sa.ForeignKeyConstraint(["org_id", "lead_id"], ["leads.org_id", "leads.id"],
                                ondelete="CASCADE", name="fk_mcp_draft_lead_same_org"),
        sa.CheckConstraint("kind = 'inquiry_reply'", name="ck_mcp_draft_kind"),
        sa.CheckConstraint("status IN ('pending_review', 'approved', 'declined')",
                           name="ck_mcp_draft_status"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 4000",
                           name="ck_mcp_draft_body_length"),
        sa.CheckConstraint("char_length(idempotency_key) BETWEEN 12 AND 80",
                           name="ck_mcp_draft_idempotency_length"),
    )
    op.create_index("ix_mcp_action_drafts_org_id", "mcp_action_drafts", ["org_id"])
    op.create_index("ix_mcp_action_drafts_lead_id", "mcp_action_drafts", ["lead_id"])
    op.create_index("ix_mcp_drafts_org_status_created", "mcp_action_drafts",
                    ["org_id", "status", "created_at"])
    op.execute("ALTER TABLE mcp_action_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_action_drafts FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON mcp_action_drafts "
               "USING (org_id = app_org()) WITH CHECK (org_id = app_org())")


def downgrade() -> None:
    # Do not lose approved or pending customer contact content during rollback.
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM mcp_action_drafts) THEN
            RAISE EXCEPTION 'Cannot downgrade: recorded OAuth draft actions exist';
          END IF;
        END $$;
    """)
    op.drop_index("ix_mcp_drafts_org_status_created", table_name="mcp_action_drafts")
    op.drop_index("ix_mcp_action_drafts_lead_id", table_name="mcp_action_drafts")
    op.drop_index("ix_mcp_action_drafts_org_id", table_name="mcp_action_drafts")
    op.drop_table("mcp_action_drafts")
    op.drop_constraint("uq_leads_org_id", "leads", type_="unique")
