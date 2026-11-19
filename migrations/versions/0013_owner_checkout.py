"""Allow an authenticated existing owner to purchase without a fabricated assessment.

Revision ID: 0013
Revises: 0012

The existing organization and owner membership are checked in the application; a
service order with assessment_id=NULL is the evidence of in-app checkout, not a
new customer onboarding assessment. Existing assessment orders remain unchanged.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("service_orders", "assessment_id", existing_type=sa.UUID(),
                    nullable=True)


def downgrade() -> None:
    # Refuse to destroy valid owner-checkout history in downgrade.
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM service_orders WHERE assessment_id IS NULL) THEN
            RAISE EXCEPTION 'Cannot downgrade: owner checkout orders without assessments exist';
          END IF;
        END $$;
    """)
    op.alter_column("service_orders", "assessment_id", existing_type=sa.UUID(),
                    nullable=False)
