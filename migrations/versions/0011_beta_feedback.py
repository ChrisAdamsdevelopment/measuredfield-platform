"""MF-080: platform-owned opt-in beta feedback, no automatic tenant access.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "beta_feedback",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("submitter_user_id", UUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("reviewed_by_user_id", UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("kind IN ('bug', 'idea', 'other')",
                           name="ck_beta_feedback_kind"),
        sa.CheckConstraint("status IN ('new', 'reviewing', 'resolved')",
                           name="ck_beta_feedback_status"),
        sa.CheckConstraint("char_length(message) BETWEEN 15 AND 1800",
                           name="ck_beta_feedback_message_length"),
    )
    op.create_index("ix_beta_feedback_submitter_user_id", "beta_feedback",
                    ["submitter_user_id"])
    op.create_index("ix_beta_feedback_status_created", "beta_feedback",
                    ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("beta_feedback")
