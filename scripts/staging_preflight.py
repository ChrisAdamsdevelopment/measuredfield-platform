"""Staging preflight: decide whether a configuration is safe to deploy to staging.

    python -B scripts/staging_preflight.py                 # check the current environment
    python -B scripts/staging_preflight.py --env-file X    # check a KEY=VALUE file instead
    python -B scripts/staging_preflight.py --database      # also inspect the live database

It only reads. It deploys nothing, changes no DNS, spends nothing and never prints a
configuration value: findings name the variable and the problem, not the content.

Exit code 0 with STAGING_PREFLIGHT_PASS means the automated part of the gate passed. It is
NOT approval to deploy: docs/STAGING_RUNBOOK.md lists the human approvals still required.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver", "0.0.0.0"}
PLACEHOLDERS = ("change_me", "changeme", "example.com", "<", "todo", "placeholder", "your-")
# Multi-label public suffixes we can recognise without the full Public Suffix List. The
# check below is a guard against an obvious mistake, not a complete PSL implementation.
TWO_LABEL_SUFFIXES = {"co.uk", "org.uk", "com.au", "co.nz", "com.br", "co.jp", "onrender.com", "pages.dev",
                      "github.io", "herokuapp.com", "vercel.app", "netlify.app", "fly.dev"}


@dataclass(frozen=True)
class Finding:
    level: str      # FAIL blocks the gate; WARN must be read by the approver
    key: str
    message: str

    def __str__(self) -> str:
        return f"{self.level:4} {self.key}: {self.message}"


def parse_env_file(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def registrable_domain(host: str) -> str:
    """Best-effort registrable domain (eTLD+1) for comparing two hostnames."""
    labels = host.lower().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _db_parts(url: str):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("postgres", "postgresql", "postgresql+psycopg") or not parts.hostname:
        return None
    return parts


def check_environment(env: dict[str, str]) -> list[Finding]:
    """Configuration-only checks. Never includes a value in a finding."""
    out: list[Finding] = []

    def fail(key: str, message: str) -> None:
        out.append(Finding("FAIL", key, message))

    def warn(key: str, message: str) -> None:
        out.append(Finding("WARN", key, message))

    def flag(key: str) -> bool:
        return env.get(key, "").strip().lower() in ("1", "true", "yes", "on")

    if env.get("MF_ENV") not in ("staging", "production"):
        fail("MF_ENV", "must be 'staging' (or 'production'); other values disable the fail-closed startup checks")

    runtime, migration = _db_parts(env.get("DATABASE_URL", "")), _db_parts(env.get("MIGRATION_DATABASE_URL", ""))
    if runtime is None:
        fail("DATABASE_URL", "missing or not a PostgreSQL URL")
    if migration is None:
        fail("MIGRATION_DATABASE_URL", "missing or not a PostgreSQL URL (needed by the pre-deploy migration step)")
    if runtime and migration:
        if not runtime.username or not migration.username or runtime.username == migration.username:
            fail("DATABASE_URL", "must use a different database role from MIGRATION_DATABASE_URL")
        if runtime.path != migration.path or not runtime.path.strip("/"):
            fail("DATABASE_URL", "must name the same database as MIGRATION_DATABASE_URL")
        if runtime.username in ("postgres", "admin", "root"):
            fail("DATABASE_URL", "runtime role has a superuser-style name; use a dedicated restricted role")
        for key, parts in (("DATABASE_URL", runtime), ("MIGRATION_DATABASE_URL", migration)):
            if parts.hostname in LOCAL_HOSTS:
                fail(key, "points at a local host")
            if not parts.password or len(parts.password) < 16:
                fail(key, "password is missing or shorter than 16 characters")

    secret = env.get("MF_SECRET_KEY", "")
    if len(secret) < 32 or secret.lower().startswith("dev-only") or len(set(secret)) < 10:
        fail("MF_SECRET_KEY", "must be a random value of at least 32 characters")

    base = urlsplit(env.get("MF_PUBLIC_BASE_URL", ""))
    if base.scheme != "https" or not base.hostname or base.path not in ("", "/"):
        fail("MF_PUBLIC_BASE_URL", "must be an https:// origin with no path")
    hosts = [h.strip().lower() for h in env.get("MF_APP_HOSTS", "").split(",") if h.strip()]
    if not hosts:
        fail("MF_APP_HOSTS", "is empty")
    if set(hosts) & LOCAL_HOSTS:
        fail("MF_APP_HOSTS", "lists a local hostname")
    if any(":" in h or "/" in h or "*" in h for h in hosts):
        fail("MF_APP_HOSTS", "entries must be bare hostnames (no scheme, port, path or wildcard)")
    if base.hostname and hosts and base.hostname.lower() not in hosts:
        fail("MF_PUBLIC_BASE_URL", "its hostname is not listed in MF_APP_HOSTS")

    sites = env.get("MF_SITES_BASE_DOMAIN", "").strip().lower().strip(".")
    if not sites or sites in LOCAL_HOSTS or sites.endswith(".localhost") or "." not in sites:
        fail("MF_SITES_BASE_DOMAIN", "must be a real domain")
    elif sites in hosts or any(h.endswith("." + sites) for h in hosts):
        fail("MF_SITES_BASE_DOMAIN", "an application host sits on or under the customer-site domain")
    elif any(registrable_domain(sites) == registrable_domain(h) for h in hosts):
        warn("MF_SITES_BASE_DOMAIN", "shares a registrable domain with an application host; customer sites "
                                      "should live on a separate registrable domain before real customers")

    if flag("MF_INLINE_JOBS"):
        warn("MF_INLINE_JOBS", "is on: jobs run inside the web process; only acceptable with one web instance and no worker")
    if flag("MF_TRUST_PROXY_HEADERS"):
        fail("MF_TRUST_PROXY_HEADERS", "must be false")
    hops = env.get("MF_TRUSTED_PROXY_HOPS", "0").strip() or "0"
    if not hops.isdigit() or int(hops) > 3:
        fail("MF_TRUSTED_PROXY_HOPS", "must be a small non-negative integer")
    elif int(hops) == 0:
        warn("MF_TRUSTED_PROXY_HOPS", "is 0: every visitor behind the proxy shares one rate-limit bucket; "
                                       "measure the real hop count on staging before public traffic")

    email, payment = env.get("MF_EMAIL_PROVIDER", "none"), env.get("MF_PAYMENT_PROVIDER", "none")
    if email not in ("none", "resend"):
        fail("MF_EMAIL_PROVIDER", "unknown provider")
    if email == "resend" and not (env.get("MF_RESEND_API_KEY") and env.get("MF_EMAIL_FROM")):
        fail("MF_EMAIL_PROVIDER", "resend selected without MF_RESEND_API_KEY and MF_EMAIL_FROM")
    if email == "none":
        warn("MF_EMAIL_PROVIDER", "is none: owners are not notified of inquiries and sign-in links need the manual procedure")
    if payment not in ("none", "stripe"):
        fail("MF_PAYMENT_PROVIDER", "unknown provider")
    if payment == "stripe":
        key = env.get("MF_STRIPE_SECRET_KEY", "")
        if not (key and env.get("MF_STRIPE_WEBHOOK_SECRET")):
            fail("MF_PAYMENT_PROVIDER", "stripe selected without both Stripe secrets")
        if key.startswith(("sk_live_", "rk_live_")) and env.get("MF_ENV") != "production":
            fail("MF_STRIPE_SECRET_KEY", "a LIVE Stripe key is configured outside production")

    for key, value in env.items():
        if key.startswith(("MF_", "DATABASE_URL", "MIGRATION_DATABASE_URL")) and value and \
                any(marker in value.lower() for marker in PLACEHOLDERS) and key not in ("MF_BRAND_NAME",):
            fail(key, "still contains a placeholder value")
    return out


def check_blueprint(text: str) -> list[Finding]:
    """render.yaml must stay a no-surprises staging blueprint."""
    out: list[Finding] = []
    services = len(re.findall(r"^\s*-\s*type:\s*(?:web|worker)\b", text, re.MULTILINE))
    if services < 2:
        out.append(Finding("FAIL", "render.yaml", "expected a web service and a worker"))
    if text.count('autoDeployTrigger: "off"') < services:
        out.append(Finding("FAIL", "render.yaml", "every service must have autoDeployTrigger off"))
    if "healthCheckPath: /healthz" not in text:
        out.append(Finding("FAIL", "render.yaml", "web service needs healthCheckPath /healthz"))
    if "preDeployCommand: python -m app.migrate" not in text:
        out.append(Finding("FAIL", "render.yaml", "migrations must run in preDeployCommand via app.migrate"))
    if "--forwarded-allow-ips" in text and "*" in text.split("--forwarded-allow-ips", 1)[1][:8]:
        out.append(Finding("FAIL", "render.yaml", "must not trust forwarded headers from every address"))
    if "--no-proxy-headers" not in text:
        out.append(Finding("WARN", "render.yaml", "start command no longer passes --no-proxy-headers"))
    worker = text.split("type: worker", 1)[1] if "type: worker" in text else ""
    if "MIGRATION_DATABASE_URL" in worker:
        out.append(Finding("FAIL", "render.yaml", "the worker must never receive MIGRATION_DATABASE_URL"))
    for match in re.finditer(r"key:\s*(\S+)\s*\n\s*value:\s*(\S.*)", text):
        key, value = match.group(1), match.group(2).strip().strip('"')
        if re.search(r"SECRET|PASSWORD|API_KEY|DATABASE_URL|TOKEN", key) and value:
            out.append(Finding("FAIL", "render.yaml", f"{key} has a literal value; use sync: false or generateValue"))
    return out


def check_database(runtime_url: str, migration_url: str = "") -> list[Finding]:
    """Read-only inspection of a live database through the roles the deployment will use."""
    from sqlalchemy import create_engine, text
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.models import TENANT_TABLES

    out: list[Finding] = []

    def normalize(url: str) -> str:
        return re.sub(r"^postgres(ql)?(\+psycopg)?://", "postgresql+psycopg://", url)

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    code_head = ScriptDirectory.from_config(config).get_current_head()
    try:
        engine = create_engine(normalize(runtime_url), connect_args={"connect_timeout": 8})
        with engine.connect() as conn:
            role = conn.execute(text(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")).one()
            if role.rolsuper or role.rolbypassrls:
                out.append(Finding("FAIL", "DATABASE_URL", "runtime role is superuser or BYPASSRLS: row-level security would not apply"))
            if role.rolcreatedb or role.rolcreaterole:
                out.append(Finding("WARN", "DATABASE_URL", "runtime role can create databases or roles"))
            if conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner = current_user")).scalar():
                out.append(Finding("FAIL", "DATABASE_URL", "runtime role owns tables; it must only hold grants"))
            if conn.execute(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")).scalar():
                out.append(Finding("FAIL", "DATABASE_URL", "runtime role can create objects in schema public"))
            state = {r[0]: (r[1], r[2]) for r in conn.execute(text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"))}
            for table in [*TENANT_TABLES, "organizations"]:
                if state.get(table) != (True, True):
                    out.append(Finding("FAIL", "database", f"row-level security is not enabled and forced on {table}"))
                elif conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() != 0:  # noqa: S608 - fixed identifiers
                    out.append(Finding("FAIL", "database", f"{table} returns rows with no tenant context"))
            try:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:  # noqa: BLE001
                revision = None
            if revision != code_head:
                out.append(Finding("FAIL", "database", f"schema revision {revision} does not match the code ({code_head}); run migrations"))
            for needed in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                if not conn.execute(text("SELECT has_table_privilege(current_user, 'leads', :p)"), {"p": needed}).scalar():
                    out.append(Finding("FAIL", "DATABASE_URL", f"runtime role lacks {needed} on tenant tables; run scripts/grant_runtime_role.sql"))
                    break
        engine.dispose()
    except Exception as exc:  # noqa: BLE001 - report the class only, never the URL
        out.append(Finding("FAIL", "DATABASE_URL", "could not inspect the database (" + type(exc).__name__ + ")"))
    if migration_url:
        try:
            engine = create_engine(normalize(migration_url), connect_args={"connect_timeout": 8})
            with engine.connect() as conn:
                owned = conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner = current_user")).scalar()
                if not owned:
                    out.append(Finding("FAIL", "MIGRATION_DATABASE_URL", "migration role does not own the schema's tables"))
                if conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
                    out.append(Finding("WARN", "MIGRATION_DATABASE_URL", "migration role is a superuser; a plain owner role is enough"))
            engine.dispose()
        except Exception as exc:  # noqa: BLE001
            out.append(Finding("FAIL", "MIGRATION_DATABASE_URL", "could not inspect the database (" + type(exc).__name__ + ")"))
    return out


def summarize(findings: list[Finding]) -> tuple[int, str]:
    fails = [f for f in findings if f.level == "FAIL"]
    warns = [f for f in findings if f.level == "WARN"]
    if fails:
        return 1, f"STAGING_PREFLIGHT_FAIL: {len(fails)} blocking, {len(warns)} warning(s)."
    return 0, (f"STAGING_PREFLIGHT_PASS: automated checks passed with {len(warns)} warning(s). "
               "Human approval is still required (docs/STAGING_RUNBOOK.md).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only staging preflight. Never prints configuration values.")
    parser.add_argument("--env-file", type=Path, help="check a KEY=VALUE file instead of the process environment")
    parser.add_argument("--database", action="store_true", help="also inspect the live database (read-only)")
    parser.add_argument("--blueprint", type=Path, default=ROOT / "render.yaml")
    args = parser.parse_args(argv)
    env = parse_env_file(args.env_file.read_text(encoding="utf-8")) if args.env_file else dict(os.environ)
    findings = check_environment(env)
    findings += check_blueprint(args.blueprint.read_text(encoding="utf-8"))
    if args.database:
        findings += check_database(env.get("DATABASE_URL", ""), env.get("MIGRATION_DATABASE_URL", ""))
    else:
        findings.append(Finding("WARN", "database", "live database not inspected (run again with --database on staging)"))
    for finding in findings:
        print(finding)
    code, line = summarize(findings)
    print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
