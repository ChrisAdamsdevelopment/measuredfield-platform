"""Row-level security for every tenant table.

Revision ID: 0002
Revises: 0001

Each tenant table gets one policy: a row is visible and writable only when its org_id
equals the transaction-local setting ``app.org_id``. FORCE makes the policy apply to the
table owner too (the application role owns the tables on a single-role managed database).
With no setting, ``app_org()`` is NULL and the comparison is never true: no rows.

Two tables need a wider read rule so a person can find their own workspaces before one
is active:

* memberships: a user may read their own rows, but ONLY while no organization is active.
  Inside an organization they see that organization's memberships and nothing else.
* organizations: readable when active, or when the current user holds a membership
  (which, by the rule above, is only discoverable with no organization active).
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "lead_notes", "leads", "proposals", "projects", "customers", "memory_items", "documents",
    "lesson_candidates", "assessment_reports", "improvements", "site_events", "site_versions",
    "sites", "data_requests", "audit_events",
]


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION app_org() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;
    """)
    op.execute("""
        CREATE FUNCTION app_user() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
    """)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING (org_id = app_org()) WITH CHECK (org_id = app_org())")

    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memberships FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON memberships "
               "USING (org_id = app_org()) WITH CHECK (org_id = app_org())")
    op.execute("CREATE POLICY own_memberships ON memberships FOR SELECT "
               "USING (app_org() IS NULL AND user_id = app_user())")

    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON organizations "
               "USING (id = app_org()) WITH CHECK (id = app_org())")
    op.execute("CREATE POLICY member_can_see ON organizations FOR SELECT "
               "USING (id IN (SELECT org_id FROM memberships WHERE user_id = app_user()))")

    # The audit trail is append-only for the application: no UPDATE policy exists beyond
    # tenant_isolation, so add an explicit restrictive rule that blocks changing history.
    op.execute("CREATE POLICY audit_no_update ON audit_events AS RESTRICTIVE FOR UPDATE USING (false)")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_no_update ON audit_events")
    op.execute("DROP POLICY IF EXISTS member_can_see ON organizations")
    op.execute("DROP POLICY IF EXISTS own_memberships ON memberships")
    for table in [*TENANT_TABLES, "memberships", "organizations"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_user()")
    op.execute("DROP FUNCTION IF EXISTS app_org()")
