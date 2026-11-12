"""Enforce referenced site ownership and preserve operational record integrity.

Revision ID: 0003
Revises: 0002

No data is silently deleted. Preexisting orphaned references cause migration failure,
requiring investigation before a real deployment.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


# Tables under FORCE ROW LEVEL SECURITY that take part in the new foreign keys.
_RLS_TABLES = ("sites", "site_versions", "organizations", "leads", "customers")


def upgrade() -> None:
    # ADD FOREIGN KEY validates existing rows with an ordinary query run as the table owner.
    # Under FORCE ROW LEVEL SECURITY the owner has no tenant context and sees ZERO rows, so
    # on any database that already holds tenant data every existing reference looks orphaned
    # and the migration aborts. Lift FORCE for the duration of this transaction only (the
    # owner then reads its own tables normally; ENABLE stays on, so every other role is still
    # restricted, and ALTER TABLE holds an exclusive lock until commit), then restore it.
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    _add_constraints()
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _add_constraints() -> None:
    # A hostname may NEVER point to a site owned by a different organization.
    op.create_unique_constraint("uq_sites_id_org_id", "sites", ["id", "org_id"])
    op.create_foreign_key("fk_site_hosts_site_owner", "site_hosts", "sites",
                          ["site_id", "org_id"], ["id", "org_id"], ondelete="CASCADE")

    # Site/domain deletion must not leave stale customer-facing hostname mappings.
    op.create_foreign_key("fk_sites_published_version", "sites", "site_versions",
                          ["published_version_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_service_orders_org", "service_orders", "organizations",
                          ["org_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_leads_site", "leads", "sites",
                          ["site_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_leads_assignee", "leads", "users",
                          ["assigned_to"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_leads_customer", "leads", "customers",
                          ["customer_id"], ["id"], ondelete="SET NULL")

    op.create_index("ix_leads_site_id", "leads", ["site_id"])
    op.create_index("ix_leads_assigned_to", "leads", ["assigned_to"])
    op.create_index("ix_leads_customer_id", "leads", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_leads_customer_id", table_name="leads")
    op.drop_index("ix_leads_assigned_to", table_name="leads")
    op.drop_index("ix_leads_site_id", table_name="leads")
    for name, table in [
        ("fk_leads_customer", "leads"),
        ("fk_leads_assignee", "leads"),
        ("fk_leads_site", "leads"),
        ("fk_service_orders_org", "service_orders"),
        ("fk_sites_published_version", "sites"),
        ("fk_site_hosts_site_owner", "site_hosts"),
    ]:
        op.drop_constraint(name, table, type_="foreignkey")
    op.drop_constraint("uq_sites_id_org_id", "sites", type_="unique")
