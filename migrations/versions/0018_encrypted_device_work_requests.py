"""MF-024: per-business encrypted remote work requests and signed device claims.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_connected_devices_org_id_id", "connected_devices", ["org_id", "id"])
    op.create_table(
        "device_work_requests",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey(
            "organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey(
            "users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("oauth_client_id", sa.String(250), nullable=False),
        sa.Column("request_key", sa.String(80), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("payload_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("lease_hash", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("result_status", sa.String(30), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "device_id"], ["connected_devices.org_id", "connected_devices.id"],
            ondelete="CASCADE", name="fk_device_work_owner_device"),
        sa.UniqueConstraint("org_id", "created_by", "request_key",
                            name="uq_device_work_request_idempotency"),
        sa.CheckConstraint("action IN ('write_file','make_directory')",
                           name="ck_device_work_action"),
        sa.CheckConstraint("status IN ('queued','leased','succeeded','failed','cancelled','expired')",
                           name="ck_device_work_status"),
    )
    op.create_index("ix_device_work_requests_org_id", "device_work_requests", ["org_id"])
    op.create_index(
        "ix_device_work_claim", "device_work_requests",
        ["org_id", "device_id", "status", "created_at"])
    op.execute("ALTER TABLE device_work_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_work_requests FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON device_work_requests "
               "USING (org_id = app_org()) WITH CHECK (org_id = app_org())")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM device_work_requests) THEN
                RAISE EXCEPTION 'Refuse downgrade while remote work requests exist';
            END IF;
        END $$;
    """)
    op.drop_table("device_work_requests")
    op.drop_constraint(
        "uq_connected_devices_org_id_id", "connected_devices", type_="unique")
