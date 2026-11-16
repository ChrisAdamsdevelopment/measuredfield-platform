"""Tenant-isolated AI Visibility Lab storage; no provider connection or automatic execution.

Revision ID: 0005
Revises: 0004

Contains four tenant tables and explicit FORCE RLS policies. A copied tenant ID
or a cross-organization question identifier can never grant access. This
migration makes no changes to existing data, payment, or public website routes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TENANT_TABLES = ("visibility_gsc_snapshots", "visibility_questions",
                 "visibility_experiments", "visibility_observations")


def upgrade():
    op.create_table(
        "visibility_gsc_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="owner_csv"),
        sa.Column("date_start", sa.Date(), nullable=True),
        sa.Column("date_end", sa.Date(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "digest", name="uq_vgs_org_digest"),
        sa.UniqueConstraint("org_id", "id", name="uq_vgs_org_id"),
    )
    op.create_index("ix_visibility_gsc_snapshots_org_id", "visibility_gsc_snapshots", ["org_id"])
    op.create_index("ix_vgs_org_created", "visibility_gsc_snapshots", ["org_id", "created_at"])
    op.create_table(
        "visibility_questions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_snapshot_id", UUID, nullable=True),
        sa.Column("question_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False, server_default="Residential remodeling"),
        sa.Column("market", sa.String(90), nullable=False, server_default="Prescott, Arizona"),
        sa.Column("origin", sa.String(30), nullable=False, server_default="gsc_suggestion"),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("source_queries", JSONB, nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "question_hash", name="uq_vq_org_hash"),
        sa.UniqueConstraint("org_id", "id", name="uq_vq_org_id"),
        sa.ForeignKeyConstraint(
            ["org_id", "source_snapshot_id"],
            ["visibility_gsc_snapshots.org_id", "visibility_gsc_snapshots.id"],
            name="fk_vq_snapshot_same_tenant"),
    )
    op.create_index("ix_visibility_questions_org_id", "visibility_questions", ["org_id"])
    op.create_index("ix_vq_org_status", "visibility_questions", ["org_id", "status"])
    op.create_table(
        "visibility_experiments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("configuration", JSONB, nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(22), nullable=False, server_default="planned"),
        sa.Column("provider_requests_executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "id", name="uq_ve_org_id"),
    )
    op.create_index("ix_visibility_experiments_org_id", "visibility_experiments", ["org_id"])
    op.create_index("ix_ve_org_created", "visibility_experiments", ["org_id", "created_at"])
    op.create_table(
        "visibility_observations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("experiment_id", UUID, nullable=True),
        sa.Column("source_run_id", sa.String(128), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("actual_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(90), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(100), nullable=False, server_default=""),
        sa.Column("grounding", JSONB, nullable=False),
        sa.Column("source_metadata", JSONB, nullable=False),
        sa.Column("reported_mentioned", sa.Boolean(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "source_run_id", name="uq_vo_org_external"),
        sa.ForeignKeyConstraint(
            ["org_id", "experiment_id"], ["visibility_experiments.org_id", "visibility_experiments.id"],
            name="fk_vo_experiment_same_tenant"),
    )
    op.create_index("ix_visibility_observations_org_id", "visibility_observations", ["org_id"])
    op.create_index("ix_vo_org_experiment", "visibility_observations", ["org_id", "experiment_id"])
    op.create_index("ix_vo_org_provenance", "visibility_observations", ["org_id", "provenance"])
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING (org_id = app_org()) WITH CHECK (org_id = app_org())")
    # Captured answer text/provenance should not be editable in place.
    op.execute("CREATE POLICY visibility_observations_no_update "
               "ON visibility_observations AS RESTRICTIVE FOR UPDATE USING (false)")


def downgrade():
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
