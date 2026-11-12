-- Apply as the database owner AFTER alembic upgrade head.
-- A separate role mf_app must already have been created with an independently
-- generated strong password. Never commit passwords or run this as mf_app.
-- Role creation itself is a one-time privileged administrative action.
ALTER ROLE mf_app NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA public TO mf_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mf_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO mf_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mf_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO mf_app;
-- Defense in depth. The app role must not own the schema or tables.
REVOKE CREATE ON SCHEMA public FROM mf_app;
-- Each tenant table MUST retain ENABLE/FORCE ROW LEVEL SECURITY.
-- Run the application isolation self-check with DATABASE_URL=mf_app.
