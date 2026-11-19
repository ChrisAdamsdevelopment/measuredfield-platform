"""One-time owner-verified OIDC challenges; no provider credentials or tokens.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "mcp_owner_oidc_challenges",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("issuer", sa.String(250), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("session_id", UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("pkce_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("state_hash", name="uq_mcp_owner_oidc_challenges_state_hash"),
        sa.CheckConstraint("length(state_hash) = 64", name="ck_oidc_link_state_hash_length"),
        sa.CheckConstraint("length(nonce_hash) = 64", name="ck_oidc_link_nonce_hash_length"),
    )
    op.create_index("ix_mcp_owner_oidc_challenges_user_id", "mcp_owner_oidc_challenges",
                    ["user_id"])
    op.create_index("ix_oidc_link_expiry", "mcp_owner_oidc_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("mcp_owner_oidc_challenges")
