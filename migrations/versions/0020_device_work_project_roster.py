"""MF-024: device-origin short-lived roster of locally approved Work projects.

Revision ID: 0020
Revises: 0019

Never stores absolute paths, tokens, local pairing secrets or customer files.
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_work_projects",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey(
            "organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.String(24), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("access", sa.String(10), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "device_id"],
            ["connected_devices.org_id", "connected_devices.id"],
            name="fk_device_work_projects_device", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("org_id", "device_id", "project_id",
                            name="uq_device_work_projects_identity"),
        sa.CheckConstraint("access IN ('readwrite')",
                           name="ck_device_work_projects_access"),
    )
    op.create_index("ix_device_work_projects_fresh", "device_work_projects",
                    ["org_id", "device_id", "expires_at"])
    op.create_index("ix_device_work_projects_org_id", "device_work_projects",
                    ["org_id"])
    op.execute("ALTER TABLE device_work_projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_work_projects FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON device_work_projects "
        "USING (org_id = app_org()) WITH CHECK (org_id = app_org())"
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM device_work_projects) THEN
                RAISE EXCEPTION 'Refuse downgrade while work project roster exists';
            END IF;
        END $$;
    """)
    op.drop_table("device_work_projects")
