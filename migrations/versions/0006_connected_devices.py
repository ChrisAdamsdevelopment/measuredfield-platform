"""Account-linked trusted computers (MF-032).

Revision ID: 0006
Revises: 0005

New tables
  connected_devices      tenant table, forced row-level security
  device_events          tenant table, forced row-level security
  device_pairing_codes   platform table: holds only a code hash and the ids needed to enter
                         the right tenant when a computer redeems a code; no private data

No existing table or row is changed, so there is nothing to repair on a populated database
and no need to lift FORCE on existing tables (see 0003 for why that matters).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")
TENANT_TABLES = ("connected_devices", "device_events")


def upgrade() -> None:
    op.create_table(
        "connected_devices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("reported_name", sa.String(80), nullable=False, server_default=""),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("access", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("check_number", sa.String(6), nullable=False, server_default=""),
        sa.Column("confirm_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", UUID, nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credential_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", UUID, nullable=True),
        sa.Column("revoked_reason", sa.String(40), nullable=False, server_default=""),
        sa.UniqueConstraint("credential_hash", name="uq_connected_devices_credential_hash"),
        sa.CheckConstraint("access IN ('read', 'suggest')", name="ck_connected_devices_access"),
        sa.CheckConstraint("status IN ('pending', 'active', 'revoked', 'expired')", name="ck_connected_devices_status"),
    )
    op.create_index("ix_connected_devices_org_id", "connected_devices", ["org_id"])
    op.create_table(
        "device_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", UUID, sa.ForeignKey("connected_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("project_label", sa.String(80), nullable=False, server_default=""),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("ix_device_events_org_id", "device_events", ["org_id"])
    op.create_index("ix_device_events_device_id", "device_events", ["device_id"])
    op.create_table(
        "device_pairing_codes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_name", sa.String(80), nullable=False),
        sa.Column("access", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("code_hash", name="uq_device_pairing_codes_code_hash"),
    )
    op.create_index("ix_device_pairing_codes_org_id", "device_pairing_codes", ["org_id"])
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING (org_id = app_org()) WITH CHECK (org_id = app_org())")


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_table("device_pairing_codes")
    op.drop_table("device_events")
    op.drop_table("connected_devices")
