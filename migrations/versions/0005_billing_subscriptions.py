"""Stripe subscription and checkout settlement facts.

Revision ID: 0005
Revises: 0004

Platform-only billing table: no tenant endpoint may query this table directly.
Signed webhooks are the sole source of subscription/invoice state changes.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("service_orders", sa.Column("settled_amount_cents", sa.Integer(), nullable=True))
    op.add_column("service_orders", sa.Column("discount_amount_cents", sa.Integer(), nullable=True))
    op.add_column("service_orders", sa.Column("tax_amount_cents", sa.Integer(), nullable=True))
    op.create_table(
        "billing_subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=False),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("org_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("invoice_status", sa.String(32), nullable=False),
        sa.Column("latest_invoice_id", sa.String(255), nullable=True),
        sa.Column("last_subscription_event_created", sa.BigInteger(), nullable=False),
        sa.Column("last_invoice_event_created", sa.BigInteger(), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("entitlement_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["service_orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_subscription_id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index("ix_billing_subscriptions_org_id", "billing_subscriptions", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_billing_subscriptions_org_id", table_name="billing_subscriptions")
    op.drop_table("billing_subscriptions")
    op.drop_column("service_orders", "tax_amount_cents")
    op.drop_column("service_orders", "discount_amount_cents")
    op.drop_column("service_orders", "settled_amount_cents")
