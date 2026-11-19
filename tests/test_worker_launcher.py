"""Owner UI-only launch; guard against shell exposure, stale account and bearer leakage."""
from __future__ import annotations

import pytest

from app.desktop_companion.worker_launcher import launch_owner_work_session
from app.local_connect.store import AccessDenied, LocalConnect
from app.paired_connect.registry import PairingRegistry


def _paired(tmp_path, *, work: bool):
    store = LocalConnect(tmp_path / "private")
    root = tmp_path / "approved-site"
    root.mkdir(parents=True)
    grant = store.grant("Site", root, "readwrite")["id"]
    registry = PairingRegistry(store.home)
    pairing = registry.create(
        "Owner approved", [grant], owner_confirmed=True,
        allow_writes=work, owner_confirmed_write=work, expires_minutes=15)
    return registry, pairing["pairing_id"]


def test_owner_launcher_process_receives_id_not_bearers(tmp_path):
    registry, pair_id = _paired(tmp_path, work=True)
    calls = []
    class Guard:
        def __init__(self, home): assert home == registry.home
        def check(self, *, action): assert action == "write"
    class Process:
        pid = 12345
    def fake_runner(command, **options):
        calls.append((command, options))
        return Process()
    proc = launch_owner_work_session(
        pair_id, registry=registry, guard_factory=Guard, runner=fake_runner)
    assert proc.pid == 12345
    command, options = calls[0]
    assert "--pairing-id" in command and pair_id in command
    assert "-m" in command and "app.desktop_companion.work_poller" in command
    assert options["shell"] is False and options["close_fds"] is True
    assert "secret" not in " ".join(command).lower()
    assert registry.token_for_local_launcher(pair_id) not in " ".join(command)


def test_launcher_rejects_read_only_and_offline_work(tmp_path):
    registry, pair_id = _paired(tmp_path, work=False)
    calls = []
    class Guard:
        def __init__(self, home): pass
        def check(self, *, action): calls.append("authorized")
    with pytest.raises(AccessDenied):
        launch_owner_work_session(
            pair_id, registry=registry, guard_factory=Guard,
            runner=lambda *a, **kw: calls.append("executed"))
    assert not calls
    registry, pair_id = _paired(tmp_path / "other", work=True)
    class OfflineGuard:
        def __init__(self, home): pass
        def check(self, *, action): raise AccessDenied("offline device")
    with pytest.raises(AccessDenied):
        launch_owner_work_session(
            pair_id, registry=registry, guard_factory=OfflineGuard,
            runner=lambda *a, **kw: calls.append("executed"))
    assert not calls
