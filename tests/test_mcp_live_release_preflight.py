"""MF-078: safe deployment metadata checks without real Render access."""
from __future__ import annotations

import json

import pytest

from scripts import mcp_live_release_preflight as release


def _service(**overrides):
    data = {
        "id": release.SERVICE_ID,
        "name": release.SERVICE_NAME,
        "repo": release.REPO_URL,
        "branch": release.EXPECTED_BRANCH,
        "serviceDetails": {"url": release.SERVICE_URL},
        "secret_field": "PRIVATE_RENDER_VALUE",
    }
    data.update(overrides)
    return data


def _deploy(status="live"):
    return {"status": status, "commit": {"id": "c" * 40},
            "private_logging_token": "PRIVATE_RENDER_VALUE"}


def _git(*, ahead=0, live_only=0, available=True):
    return {"available": available, "local_ahead": ahead,
            "live_only": live_only, "local_head": "a" * 40}


def _report(*, service=None, deploy=None, env_keys=None, git=None):
    return release.assess_release(
        service=service if service is not None else _service(),
        deploy=deploy if deploy is not None else _deploy(),
        env_keys=env_keys if env_keys is not None else set(release.REQUIRED_NAMES),
        git=git if git is not None else _git(),
    )


def test_nominal_metadata_never_claims_live_oauth_consent():
    result = _report()
    assert result["release_preflight"] == "metadata_only_manual_gates"
    assert result["safe_to_enable_mcp"] is False
    assert result["chatgpt_plugin_connected"] is False
    assert result["oauth_provider_audience_verified"] is False
    assert result["owner_consent_verified"] is False
    assert result["revocation_verified"] is False


def test_missing_critical_env_names_block_and_do_not_echo_values():
    keys = {"MF_AUTH0_CLIENT_SECRET", "MF_SECRET_KEY"}
    result = _report(env_keys=keys)
    assert result["release_preflight"] == "blocked"
    assert sorted(result["missing_environment_names"]) == sorted(release.REQUIRED_NAMES)
    assert "MF_AUTH0_CLIENT_SECRET" not in str(result)
    assert "PRIVATE_RENDER_VALUE" not in json.dumps(result)
    assert result["environment_group_inheritance_checked"] is False


def test_ahead_behind_divergence_is_explicit_blocker():
    result = _report(git=_git(ahead=55, live_only=2))
    assert "PRODUCTION_BRANCH_DIVERGED" in result["blockers"]
    assert "LIVE_AND_TESTED_SOURCE_DIFFER" in result["blockers"]
    assert result["source_state"]["local_ahead_commits"] == 55
    assert result["source_state"]["production_only_commits"] == 2
    assert not result["safe_to_enable_mcp"]


def test_latest_source_is_not_same_as_running_revision():
    result = _report(git=_git(ahead=54))
    assert "LIVE_AND_TESTED_SOURCE_DIFFER" in result["blockers"]
    assert "PRODUCTION_BRANCH_DIVERGED" not in result["blockers"]


def test_wrong_service_host_branch_or_repo_are_all_denied():
    bad = _service(id="different", repo="https://attacker.invalid",
                   branch="main", serviceDetails={"url": "https://fake.invalid"})
    result = _report(service=bad)
    assert set(result["blockers"]) >= {
        "WRONG_RENDER_SERVICE", "REPO_MISMATCH", "PRODUCTION_BRANCH_CHANGED",
        "PUBLIC_HOST_MISMATCH",
    }


def test_failed_deploy_and_unknown_git_not_ready():
    result = _report(deploy=_deploy(status="pre_deploy_failed"),
                     git={"available": False})
    assert "DEPLOY_NOT_LIVE" in result["blockers"]
    assert "LOCAL_GIT_ANCESTRY_UNVERIFIED" in result["blockers"]


def test_null_nested_render_metadata_fails_closed():
    result = _report(service=_service(serviceDetails=None),
                     deploy={"status": "live", "commit": None})
    assert "PUBLIC_HOST_MISMATCH" in result["blockers"]
    assert "NO_VALID_LIVE_COMMIT" in result["blockers"]
    assert result["safe_to_enable_mcp"] is False


@pytest.mark.parametrize("bad", ("", "123", "G" * 40, "A" * 40))
def test_bad_live_sha_never_invokes_git(monkeypatch, bad):
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *args, **kwargs: pytest.fail("bad SHA spawned git"))
    assert release._git_counts(bad) == {"available": False}


def test_render_fetch_rejects_arbitrary_metadata_path(monkeypatch):
    monkeypatch.setattr(release, "build_opener",
                        lambda *args: pytest.fail("unsupported path opened network"))
    with pytest.raises(ValueError):
        release._read_render_json(path="/services/other-id/env-vars", key="synthetic")


def test_live_report_parses_only_environment_names(monkeypatch):
    seen = []
    def fake_fetch(*, path, key):
        seen.append((path, key))
        if path.endswith("/env-vars?limit=100"):
            return [{"envVar": {"key": x, "value": "VERY_PRIVATE_VALUE"}}
                    for x in release.REQUIRED_NAMES]
        if path.endswith("/deploys?limit=5"):
            return [{"deploy": _deploy()}]
        return _service()
    monkeypatch.setattr(release, "_read_render_json", fake_fetch)
    monkeypatch.setattr(release, "_git_counts", lambda sha: _git())
    result = release.live_report("SYNTHETIC_RENDER_KEY")
    raw = json.dumps(result)
    assert "VERY_PRIVATE_VALUE" not in raw
    assert "SYNTHETIC_RENDER_KEY" not in raw
    assert "PRIVATE_RENDER_VALUE" not in raw
    assert len(seen) == 3
    assert result["missing_environment_names"] == []


def test_main_offline_cannot_read_live_service(monkeypatch, capsys):
    monkeypatch.setattr(release, "live_report",
                        lambda key: pytest.fail("default CLI contacted Render"))
    assert release.main(["--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["release_preflight"] == "not_probed"
    assert report["chatgpt_plugin_connected"] is False


def test_main_live_failure_does_not_reflect_error_or_key(monkeypatch, capsys):
    monkeypatch.setenv("RENDER_API_KEY", "VERY_PRIVATE_RENDER_TOKEN")
    monkeypatch.setattr(release, "live_report",
                        lambda key: (_ for _ in ()).throw(
                            ValueError("VERY_PRIVATE_RENDER_TOKEN")))
    assert release.main(["--live", "--json"]) == 2
    raw = capsys.readouterr().out
    assert "VERY_PRIVATE_RENDER_TOKEN" not in raw
    assert json.loads(raw)["release_preflight"] == "render_read_failed"
