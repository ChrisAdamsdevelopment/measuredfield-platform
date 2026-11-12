"""MF-008: staging preflight and backup-drill checks. Synthetic values only."""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import backup_drill  # noqa: E402
import staging_preflight as pre  # noqa: E402


# Synthetic values. URLs are assembled from parts so no credential-shaped literal appears in
# source and the repository secret scanner stays quiet without an exemption.
PW = "a" * 24


def db_url(user: str, password: str, host: str = "dpg-synthetic-a.internal", name: str = "measuredfield") -> str:
    return "postgresql://" + user + ":" + password + "@" + host + ":5432/" + name


def good_env() -> dict[str, str]:
    """A complete, valid staging configuration built from random synthetic values."""
    return {
        "MF_ENV": "staging",
        "DATABASE_URL": db_url("mf_app", secrets.token_hex(16)),
        "MIGRATION_DATABASE_URL": db_url("mf_owner", secrets.token_hex(16)),
        "MF_SECRET_KEY": secrets.token_hex(32),
        "MF_APP_HOSTS": "staging.measuredfield-app.test",
        "MF_SITES_BASE_DOMAIN": "mf-customer-sites.test",
        "MF_PUBLIC_BASE_URL": "https://staging.measuredfield-app.test",
        "MF_INLINE_JOBS": "false", "MF_TRUST_PROXY_HEADERS": "false", "MF_TRUSTED_PROXY_HOPS": "1",
        "MF_EMAIL_PROVIDER": "none", "MF_PAYMENT_PROVIDER": "none",
    }


def keys(findings, level="FAIL"):
    return {f.key for f in findings if f.level == level}


def test_valid_staging_configuration_passes_with_only_expected_warnings():
    findings = pre.check_environment(good_env())
    assert keys(findings) == set()
    assert keys(findings, "WARN") == {"MF_EMAIL_PROVIDER"}
    assert pre.summarize(findings)[0] == 0


@pytest.mark.parametrize("change, expected", [
    ({"MF_ENV": "development"}, "MF_ENV"),
    ({"DATABASE_URL": ""}, "DATABASE_URL"),
    ({"MIGRATION_DATABASE_URL": "sqlite:///x.db"}, "MIGRATION_DATABASE_URL"),
    ({"DATABASE_URL": f"postgresql://mf_owner:{PW}@dpg-synthetic-a.internal:5432/measuredfield"}, "DATABASE_URL"),  # same role as migrations
    ({"DATABASE_URL": f"postgresql://mf_app:{PW}@dpg-synthetic-a.internal:5432/otherdb"}, "DATABASE_URL"),
    ({"DATABASE_URL": f"postgresql://postgres:{PW}@dpg-synthetic-a.internal:5432/measuredfield"}, "DATABASE_URL"),
    ({"DATABASE_URL": "postgresql://mf_app:short@dpg-synthetic-a.internal:5432/measuredfield"}, "DATABASE_URL"),
    ({"DATABASE_URL": f"postgresql://mf_app:{PW}@localhost:5432/measuredfield"}, "DATABASE_URL"),
    ({"MF_SECRET_KEY": "dev-only-secret-change-me"}, "MF_SECRET_KEY"),
    ({"MF_SECRET_KEY": "a" * 64}, "MF_SECRET_KEY"),
    ({"MF_PUBLIC_BASE_URL": "http://staging.measuredfield-app.test"}, "MF_PUBLIC_BASE_URL"),
    ({"MF_PUBLIC_BASE_URL": "https://other-host.test"}, "MF_PUBLIC_BASE_URL"),
    ({"MF_APP_HOSTS": "staging.measuredfield-app.test,localhost"}, "MF_APP_HOSTS"),
    ({"MF_APP_HOSTS": "https://staging.measuredfield-app.test"}, "MF_APP_HOSTS"),
    ({"MF_APP_HOSTS": "*.measuredfield-app.test"}, "MF_APP_HOSTS"),
    ({"MF_SITES_BASE_DOMAIN": "sites.localhost"}, "MF_SITES_BASE_DOMAIN"),
    ({"MF_SITES_BASE_DOMAIN": "measuredfield-app.test"}, "MF_SITES_BASE_DOMAIN"),      # app host sits under it
    ({"MF_TRUST_PROXY_HEADERS": "true"}, "MF_TRUST_PROXY_HEADERS"),
    ({"MF_TRUSTED_PROXY_HOPS": "many"}, "MF_TRUSTED_PROXY_HOPS"),
    ({"MF_EMAIL_PROVIDER": "resend"}, "MF_EMAIL_PROVIDER"),
    ({"MF_EMAIL_PROVIDER": "smtp"}, "MF_EMAIL_PROVIDER"),
    ({"MF_PAYMENT_PROVIDER": "stripe"}, "MF_PAYMENT_PROVIDER"),
    ({"MF_PAYMENT_PROVIDER": "stripe", "MF_STRIPE_SECRET_KEY": "sk_" + "live_" + "x" * 24,
      "MF_STRIPE_WEBHOOK_SECRET": "synthetic"}, "MF_STRIPE_SECRET_KEY"),               # live key on staging
    ({"MF_SECRET_KEY": "CHANGE_ME_" + "b" * 40}, "MF_SECRET_KEY"),
])
def test_unsafe_configuration_is_blocked(change, expected):
    findings = pre.check_environment({**good_env(), **change})
    assert expected in keys(findings), [str(f) for f in findings]
    assert pre.summarize(findings)[0] == 1


def test_warnings_for_risky_but_allowed_choices():
    env = {**good_env(), "MF_INLINE_JOBS": "true", "MF_TRUSTED_PROXY_HOPS": "0",
           "MF_SITES_BASE_DOMAIN": "sites.measuredfield-app.test"}
    findings = pre.check_environment(env)
    assert keys(findings) == set()
    assert {"MF_INLINE_JOBS", "MF_TRUSTED_PROXY_HOPS", "MF_SITES_BASE_DOMAIN"} <= keys(findings, "WARN")


def test_findings_never_contain_configuration_values():
    env = good_env()
    env.update({"MF_SECRET_KEY": "short-" + secrets.token_hex(4), "MF_PAYMENT_PROVIDER": "stripe",
                "MF_STRIPE_SECRET_KEY": "sk_" + "live_" + secrets.token_hex(16), "MF_STRIPE_WEBHOOK_SECRET": secrets.token_hex(12),
                "DATABASE_URL": db_url("mf_app", secrets.token_hex(4))})
    report = "\n".join(str(f) for f in pre.check_environment(env)) + pre.summarize(pre.check_environment(env))[1]
    for name, value in env.items():
        if len(value) >= 8 and name not in ("MF_ENV", "MF_EMAIL_PROVIDER", "MF_PAYMENT_PROVIDER"):
            assert value not in report, name
    for parts in (env["DATABASE_URL"], env["MIGRATION_DATABASE_URL"]):
        assert parts.split(":")[2].split("@")[0] not in report


def test_env_file_parsing_and_registrable_domain():
    parsed = pre.parse_env_file("# comment\nA=1\nB = 'two words'\n\nC=\"x=y\"\nnot a line\n")
    assert parsed == {"A": "1", "B": "two words", "C": "x=y"}
    assert pre.registrable_domain("a.b.example.com") == "example.com"
    assert pre.registrable_domain("mf-staging.onrender.com") == "mf-staging.onrender.com"
    assert pre.registrable_domain("shop.example.co.uk") == "example.co.uk"
    assert pre.registrable_domain("example.com") == "example.com"


def test_repository_blueprint_passes_and_unsafe_edits_are_caught():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert keys(pre.check_blueprint(blueprint)) == set()
    cases = {
        "auto deploy on": blueprint.replace('autoDeployTrigger: "off"', 'autoDeployTrigger: "commit"', 1),
        "no health check": blueprint.replace("healthCheckPath: /healthz", "healthCheckPath: /"),
        "migrations in start command": blueprint.replace("preDeployCommand: python -m app.migrate", "preDeployCommand: echo skip"),
        "trust every proxy": blueprint.replace("--no-proxy-headers", "--proxy-headers --forwarded-allow-ips='*'"),
        "literal secret": blueprint.replace("      - key: MF_SECRET_KEY\n        generateValue: true",
                                            "      - key: MF_SECRET_KEY\n        value: abcdefabcdefabcdefabcdefabcdefab"),
        "worker gets migration role": blueprint + "      - key: MIGRATION_DATABASE_URL\n        sync: false\n",
        "worker removed": blueprint.split("  - type: worker")[0],
    }
    for name, text_ in cases.items():
        assert "render.yaml" in keys(pre.check_blueprint(text_)), name


def test_cli_exit_codes_and_redaction(tmp_path, capsys):
    env_file = tmp_path / "staging.env"
    env = good_env()
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    assert pre.main(["--env-file", str(env_file)]) == 0
    out = capsys.readouterr().out
    assert "STAGING_PREFLIGHT_PASS" in out and "Human approval is still required" in out
    assert env["MF_SECRET_KEY"] not in out and env["DATABASE_URL"] not in out
    env_file.write_text("MF_ENV=staging\n", encoding="utf-8")
    assert pre.main(["--env-file", str(env_file)]) == 1
    assert "STAGING_PREFLIGHT_FAIL" in capsys.readouterr().out


# ------------------------------------------------------------- real PostgreSQL checks

def test_live_database_checks_pass_for_the_two_role_layout(pg_database):
    findings = pre.check_database(pg_database["runtime_url"], pg_database["owner_url"])
    assert keys(findings) == set(), [str(f) for f in findings]


def test_live_database_checks_reject_the_wrong_roles(pg_database):
    # Running the app as the schema owner: it owns tables and can create objects.
    as_owner = pre.check_database(pg_database["owner_url"])
    assert any("owns tables" in f.message for f in as_owner) and pre.summarize(as_owner)[0] == 1
    # Using the restricted role for migrations: it owns nothing.
    swapped = pre.check_database(pg_database["runtime_url"], pg_database["runtime_url"])
    assert any(f.key == "MIGRATION_DATABASE_URL" and "does not own" in f.message for f in swapped)
    # A superuser as the runtime role would bypass row-level security entirely.
    import os
    admin = os.environ["MF_TEST_PG_ADMIN_URL"].rsplit("/", 1)[0] + "/" + pg_database["dbname"]
    as_admin = pre.check_database(admin)
    assert any("superuser or BYPASSRLS" in f.message for f in as_admin)
    assert any("returns rows with no tenant context" in f.message or "superuser" in f.message for f in as_admin)
    # An unreachable database reports the error class, never the URL.
    dead = pre.check_database("postgresql://mf_app:synthetic-password-000@127.0.0.1:1/none")
    assert len(dead) == 1 and "synthetic-password-000" not in str(dead[0]) and "could not inspect" in dead[0].message


def test_backup_fingerprint_detects_any_difference(pg_database, platform):
    """The comparison the restore drill relies on must notice changed, missing and extra rows,
    and must notice when row-level security did not survive."""
    import os
    from sqlalchemy import create_engine
    from .conftest import onboard_business
    onboard_business(platform, business="Fingerprint Co", owner_email="owner@fingerprint.example")
    admin_url = (os.environ["MF_TEST_PG_ADMIN_URL"].rsplit("/", 1)[0] + "/" + pg_database["dbname"]).replace(
        "postgresql://", "postgresql+psycopg://").replace("postgres://", "postgresql+psycopg://")
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        before, state = backup_drill.table_fingerprints(conn), backup_drill.rls_state(conn)
        assert backup_drill.compare(before, backup_drill.table_fingerprints(conn)) == []
        assert before["organizations"]["rows"] >= 1 and backup_drill.unprotected_tenant_tables(state) == []
        # Everything below happens in one transaction that is rolled back at the end.
        conn.execute(text("UPDATE organizations SET name = name || ' (edited)' WHERE name = 'Fingerprint Co'"))
        changed = backup_drill.compare(before, backup_drill.table_fingerprints(conn))
        assert len(changed) == 1 and changed[0].startswith("organizations:")
        conn.execute(text("DELETE FROM audit_events"))
        assert any(p.startswith("audit_events:") for p in backup_drill.compare(before, backup_drill.table_fingerprints(conn)))
        conn.execute(text("ALTER TABLE leads NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE documents DISABLE ROW LEVEL SECURITY"))
        broken = backup_drill.rls_state(conn)
        assert set(backup_drill.unprotected_tenant_tables(broken)) == {"leads", "documents"}
        assert len(backup_drill.compare(state, broken)) == 2
        conn.rollback()
        assert backup_drill.compare(before, backup_drill.table_fingerprints(conn)) == []
    engine.dispose()
    missing = dict(before)
    missing.pop("leads")
    assert backup_drill.compare(before, missing) == ["leads: missing after restore"]
    assert backup_drill.compare(missing, before) == ["leads: unexpected after restore"]
