"""Stop storing usable sign-in links; record how each link was issued.

Revision ID: 0004
Revises: 0003

Schema
  auth_tokens   + issued_at, issue_count, issued_via, issued_by
  email_outbox  + auth_token_id (FK auth_tokens, SET NULL)

Data repair (runs on any database that already has rows)
  Before this revision, invitation and password-reset emails were stored with the complete
  link in email_outbox.body, readable by operators and present in every backup taken since.
  Those links must be treated as exposed, so this migration:
    1. replaces every token value in stored message bodies with a placeholder, and
    2. revokes every sign-in token that has not been used yet (unusable hash, expired now).
  People with a pending invitation or reset must be sent a new one. Nothing else is lost.

Both tables are platform tables without row-level security, so the owner can read and
repair every row here (unlike the tenant tables handled in revision 0003).
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

PLACEHOLDER = "{{AUTH_LINK_TOKEN}}"


def upgrade() -> None:
    op.add_column("auth_tokens", sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("auth_tokens", sa.Column("issue_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("auth_tokens", sa.Column("issued_via", sa.String(length=20), nullable=True))
    op.add_column("auth_tokens", sa.Column("issued_by", sa.String(length=254), nullable=True))
    op.add_column("email_outbox", sa.Column("auth_token_id", sa.UUID(), nullable=True))
    op.create_index("ix_email_outbox_auth_token_id", "email_outbox", ["auth_token_id"])
    op.create_foreign_key("fk_email_outbox_auth_token", "email_outbox", "auth_tokens",
                          ["auth_token_id"], ["id"], ondelete="SET NULL")

    # 1. No stored message keeps a token value, whatever kind it is.
    op.execute(sa.text(
        "UPDATE email_outbox SET body = regexp_replace(body, '([?&]token=)[A-Za-z0-9_-]{16,}', :repl, 'g') "
        "WHERE body ~ '[?&]token=[A-Za-z0-9_-]{16,}'").bindparams(repl="\\1" + PLACEHOLDER))
    # 2. Every unused token was readable in the outbox: revoke it. The new hash is derived
    #    from a random UUID that is never stored, so it matches no link in existence.
    op.execute(
        "UPDATE auth_tokens SET token_hash = encode(sha256(convert_to(gen_random_uuid()::text || id::text, 'UTF8')), 'hex'), "
        "expires_at = LEAST(expires_at, now()) WHERE used_at IS NULL")


def downgrade() -> None:
    # The data repair is deliberately not reversed: revoked links stay revoked.
    op.drop_constraint("fk_email_outbox_auth_token", "email_outbox", type_="foreignkey")
    op.drop_index("ix_email_outbox_auth_token_id", table_name="email_outbox")
    op.drop_column("email_outbox", "auth_token_id")
    op.drop_column("auth_tokens", "issued_by")
    op.drop_column("auth_tokens", "issued_via")
    op.drop_column("auth_tokens", "issue_count")
    op.drop_column("auth_tokens", "issued_at")
