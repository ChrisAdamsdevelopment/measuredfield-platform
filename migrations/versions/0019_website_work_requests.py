"""MF Website Work: real owner-requested ChatGPT website draft changes.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "website_work_requests",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("site_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("expected_draft_sha256", sa.String(64), nullable=False),
        sa.Column("applied_payload_sha256", sa.String(64)),
        sa.Column("resulting_draft_sha256", sa.String(64)),
        sa.Column("completed_by", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["site_id", "org_id"], ["sites.id", "sites.org_id"],
                                name="fk_website_work_owner_site", ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('queued', 'completed', 'cancelled')",
                           name="ck_website_work_status"),
    )
    op.create_index("ix_website_work_requests_org_id", "website_work_requests", ["org_id"])
    op.create_index("ix_website_work_queue", "website_work_requests",
                    ["org_id", "status", "created_at"])
    op.execute("ALTER TABLE website_work_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE website_work_requests FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON website_work_requests "
               "USING (org_id = app_org()) WITH CHECK (org_id = app_org())")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM website_work_requests) THEN
                RAISE EXCEPTION 'Refuse downgrade while website work requests exist';
            END IF;
        END $$;
    """)
    op.drop_table("website_work_requests")
