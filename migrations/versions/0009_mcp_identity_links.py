"""Verified and revocable account-to-OAuth-subject links for read-only MCP.

Revision ID: 0009
Revises: 0008
No owner enrollments, OAuth access tokens, provider keys or customer data.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "mcp_identity_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("issuer", sa.String(250), nullable=False),
        sa.Column("subject", sa.String(250), nullable=False),
        sa.Column("user_id", UUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verified_method", sa.String(24), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("issuer", "subject", name="uq_mcp_identity_issuer_subject"),
        sa.CheckConstraint("verified_method = 'oidc_pkce_owner'",
                           name="ck_mcp_identity_verified_method"),
    )
    op.create_index("ix_mcp_identity_links_user_id", "mcp_identity_links", ["user_id"])


def downgrade() -> None:
    op.drop_table("mcp_identity_links")
