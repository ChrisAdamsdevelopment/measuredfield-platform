"""MF-081: read-only release-gate synthetic fixtures, no Render API access."""
from __future__ import annotations

import json

import pytest

from scripts import mf081_public_release_gate as gate


def _service(**overrides):
    value = {
        "id": gate.SERVICE,
        "name": "measuredfield-production-web",
        "branch": "main",
        "autoDeploy": "no",
        "serviceDetails": {
            "runtime": "docker",
            "envSpecificDetails": {"preDeployCommand": gate.PREDEPLOY},
        },
        "fake_secret": "DO_NOT_DISPLAY",
    }
    value.update(overrides)
    return value


def _database(**overrides):
    value = {
        "id": gate.DB, "status": "available", "plan": "free",
        "expiresAt": "2026-12-13T18:00:16.159116Z",
    }
    value.update(overrides)
    return value


def _assess(*, service=None, db=None, restore=False, names=None):
    return gate.evaluate(
        service=service or _service(),
        deploy={"status": "live", "commit": {"id": "6c13a992" + "0" * 32}},
        database=db or _database(),
        env_names=names if names is not None else {
            "DATABASE_URL", "MIGRATION_DATABASE_URL", "MF_SECRET_KEY",
            "MF_PAYMENT_PROVIDER", "MF_EMAIL_PROVIDER", "MF_AI_PROVIDER",
        },
        backup_restore_verified=restore,
    )


def test_free_database_fails_even_with_healthy_web_service():
    result = _assess()
    assert result["gate"] == "blocked"
    assert "FREE_DATABASE_HAS_NO_MANAGED_RECOVERY" in result["blockers"]
    assert "NO_VERIFIED_PRODUCTION_RESTORE" in result["blockers"]
    assert result["migration_command_verified"] is True
    assert result["source_branch"] == "main"
    assert result["may_deploy_without_explicit_approval"] is False


def test_paid_db_not_sufficient_without_restored_backup():
    result = _assess(db=_database(plan="basic-256mb"))
    assert "NO_VERIFIED_PRODUCTION_RESTORE" in result["blockers"]
    assert result["gate"] == "blocked"


def test_even_manual_backup_confirmation_never_grants_automatic_deploy():
    result = _assess(db=_database(plan="basic-256mb"), restore=True)
    assert result["gate"] == "metadata_only_requires_operator_approval"
    assert result["may_deploy_without_explicit_approval"] is False
    assert result["chatgpt_oauth_verified"] is False
    assert result["mcp_connected"] is False


def test_wrong_branch_or_predeploy_never_passes():
    result = _assess(
        service=_service(branch="feature/old", serviceDetails={
            "runtime": "docker",
            "envSpecificDetails": {"preDeployCommand": None},
        }),
        db=_database(plan="basic-256mb"), restore=True,
    )
    assert "NOT_ON_MAIN" in result["blockers"]
    assert "MIGRATION_COMMAND_UNVERIFIED" in result["blockers"]


def test_bad_nested_details_deny_release():
    result = _assess(service=_service(serviceDetails={}),
                     db=_database(plan="basic-256mb"), restore=True)
    assert "UNEXPECTED_RUNTIME" in result["blockers"]
    assert "MIGRATION_COMMAND_UNVERIFIED" in result["blockers"]


def test_readonly_url_allowlist_no_egress_on_wrong_path(monkeypatch):
    monkeypatch.setattr(gate, "build_opener",
                        lambda *args: pytest.fail("should not make network request"))
    with pytest.raises(ValueError):
        gate.read_metadata("/services/untrusted", "synthetic")


def test_live_metadata_extracts_keys_only_not_values(monkeypatch):
    requests = []
    def fake_read(path, key):
        requests.append((path, key))
        if path.endswith("/env-vars?limit=100"):
            return [{"envVar": {"key": x, "value": "SECRET_VALUE"}}
                    for x in ("DATABASE_URL", "MIGRATION_DATABASE_URL",
                              "MF_SECRET_KEY", "MF_PAYMENT_PROVIDER",
                              "MF_EMAIL_PROVIDER", "MF_AI_PROVIDER")]
        if path.endswith("/deploys?limit=1"):
            return [{"deploy": {"status": "live", "commit": {"id": "abc" * 13 + "a"}}}]
        if "/postgres/" in path:
            return _database()
        return _service()
    monkeypatch.setattr(gate, "read_metadata", fake_read)
    result = gate.live_report("FAKE_RENDER_TOKEN")
    assert len(requests) == 4
    assert "SECRET_VALUE" not in json.dumps(result)
    assert "FAKE_RENDER_TOKEN" not in json.dumps(result)
    assert result["gate"] == "blocked"


def test_cli_offline_never_reads_remote(monkeypatch, capsys):
    monkeypatch.setattr(gate, "live_report",
                        lambda _: pytest.fail("offline CLI contacted Render"))
    monkeypatch.setattr("sys.argv", ["gate"])
    assert gate.main() == 2
    assert json.loads(capsys.readouterr().out)["gate"] == "not_probed"
