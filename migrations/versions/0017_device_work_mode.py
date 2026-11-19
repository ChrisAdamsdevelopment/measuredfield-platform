"""MF-024: owner-approved device Work access, in addition to read and suggest.

Revision ID: 0017
Revises: 0016
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_connected_devices_access", "connected_devices", type_="check")
    op.create_check_constraint(
        "ck_connected_devices_access", "connected_devices",
        "access IN ('read', 'suggest', 'work')",
    )


def downgrade() -> None:
    # Avoid silently converting owner-granted Work computers to Suggest access.
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM connected_devices WHERE access = 'work') THEN
            RAISE EXCEPTION 'Refuse downgrade while Work-mode devices exist';
          END IF;
        END $$;
    """)
    op.drop_constraint("ck_connected_devices_access", "connected_devices", type_="check")
    op.create_check_constraint(
        "ck_connected_devices_access", "connected_devices",
        "access IN ('read', 'suggest')",
    )
