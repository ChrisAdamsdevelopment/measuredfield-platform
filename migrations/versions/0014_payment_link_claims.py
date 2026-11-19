"""Pending direct Stripe Payment Link purchase intent; no email-only provisioning.

Revision ID: 0014
Revises: 0013
All fields are platform-only purchase evidence; tenant authorization is
implemented via verified authentication and exact org membership in the router.
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_link_claims",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("checkout_session_id", sa.String(255), nullable=False, unique=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=False, unique=True),
        sa.Column("stripe_customer_id", sa.String(255), nullable=False),
        sa.Column("stripe_payment_link_id", sa.String(255), nullable=False),
        sa.Column("stripe_price_id", sa.String(255), nullable=False),
        sa.Column("offer_code", sa.String(40), nullable=False),
        sa.Column("billing_email", sa.String(254), nullable=False),
        sa.Column("amount_subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("amount_total_cents", sa.Integer(), nullable=False),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column("tax_cents", sa.Integer(), nullable=False),
        sa.Column("stripe_event_id", sa.String(255), nullable=False),
        sa.Column("subscription_status", sa.String(32), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="SET NULL")),
        sa.Column("order_id", sa.UUID(), sa.ForeignKey("service_orders.id", ondelete="SET NULL"), unique=True),
        sa.Column("claimed_by_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending_claim', 'claimed', 'expired', 'needs_review')",
                           name="ck_payment_link_claim_status"),
        sa.CheckConstraint("amount_subtotal_cents > 0 AND amount_total_cents >= 0 "
                           "AND discount_cents BETWEEN 0 AND amount_subtotal_cents "
                           "AND tax_cents >= 0 AND "
                           "amount_total_cents = amount_subtotal_cents - discount_cents + tax_cents",
                           name="ck_payment_link_claim_amounts"),
    )
    op.create_index("ix_payment_link_claims_billing_email", "payment_link_claims", ["billing_email"])
    op.create_index("ix_payment_link_claims_status", "payment_link_claims", ["status"])
    op.create_table(
        "payment_link_claim_reviews",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("claim_id", sa.UUID(),
                  sa.ForeignKey("payment_link_claims.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer_user_id", sa.UUID(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action = 'recheck'", name="ck_payment_link_review_action"),
        sa.CheckConstraint("outcome IN ('reopened', 'needs_review', 'unavailable')",
                           name="ck_payment_link_review_outcome"),
    )
    op.create_index("ix_payment_link_claim_reviews_claim_id",
                    "payment_link_claim_reviews", ["claim_id"])


def downgrade() -> None:
    # Financial ownership evidence cannot be discarded after any purchase.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM payment_link_claims) THEN
                RAISE EXCEPTION 'Cannot drop direct purchases: claims exist';
            END IF;
        END $$;
    """)
    op.drop_index("ix_payment_link_claim_reviews_claim_id", table_name="payment_link_claim_reviews")
    op.drop_table("payment_link_claim_reviews")
    op.drop_index("ix_payment_link_claims_status", table_name="payment_link_claims")
    op.drop_index("ix_payment_link_claims_billing_email", table_name="payment_link_claims")
    op.drop_table("payment_link_claims")
