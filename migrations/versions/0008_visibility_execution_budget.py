"""Tenant AI visibility owner budget and idempotent reservation gate (no provider calls).

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TABLES = ("visibility_research_budgets", "visibility_research_reservations")


def upgrade():
    op.create_table(
        "visibility_research_budgets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column("ceiling_micros", sa.BigInteger(), nullable=False),
        sa.Column("held_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reconciled_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("authorized_by", UUID, nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "period_month", name="uq_vrb_org_month"),
        sa.UniqueConstraint("org_id", "id", name="uq_vrb_org_id"),
        sa.CheckConstraint("ceiling_micros >= 0 AND ceiling_micros <= 500000000", name="ck_vrb_ceiling"),
        sa.CheckConstraint("held_micros >= 0 AND reconciled_micros >= 0", name="ck_vrb_positive"),
        sa.CheckConstraint("held_micros + reconciled_micros <= ceiling_micros",
                           name="ck_vrb_within_ceiling"),
    )
    op.create_index("ix_visibility_research_budgets_org_id", "visibility_research_budgets", ["org_id"])
    op.create_table(
        "visibility_research_reservations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("budget_id", UUID, nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("planned_run_id", sa.String(20), nullable=False),
        sa.Column("plan_signature", sa.String(64), nullable=False),
        sa.Column("request_signature", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("ceiling_micros", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="reserved"),
        sa.Column("approved_by", UUID, nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True)),
        sa.Column("provider_response_id", sa.String(200)),
        sa.UniqueConstraint("org_id", "idempotency_key", name="uq_vrr_org_idempotency"),
        sa.ForeignKeyConstraint(
            ["org_id", "budget_id"],
            ["visibility_research_budgets.org_id", "visibility_research_budgets.id"],
            name="fk_vrr_budget_same_tenant", ondelete="RESTRICT"),
        sa.CheckConstraint("ceiling_micros > 0 AND ceiling_micros <= 100000000",
                           name="ck_vrr_ceiling"),
        sa.CheckConstraint("status IN ('reserved','cancelled','dispatch_unknown','reconciled')",
                           name="ck_vrr_status"),
    )
    op.create_index("ix_visibility_research_reservations_org_id", "visibility_research_reservations", ["org_id"])
    op.create_index("ix_vrr_org_status", "visibility_research_reservations", ["org_id", "status"])
    op.create_index("ix_vrr_org_budget", "visibility_research_reservations", ["org_id", "budget_id"])
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING (org_id = app_org()) WITH CHECK (org_id = app_org())")


def downgrade():
    op.drop_table("visibility_research_reservations")
    op.drop_table("visibility_research_budgets")
