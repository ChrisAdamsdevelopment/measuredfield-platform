"""Synthetic owner-interface verification; never shares real business files."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.local_connect import AccessDenied, ConflictError, LocalConnect
from app.local_connect_ui import OwnerWorkspaceController, OwnerReviewRequired
from app.local_connect_ui.controller import safe_display


@pytest.fixture
def owner(tmp_path: Path):
    project = tmp_path / "synthetic_business"
    project.mkdir()
    (project / "homepage.txt").write_text("Original website\n", encoding="utf-8")
    storage = LocalConnect(tmp_path / "private_state")
    return OwnerWorkspaceController(storage), project


def test_empty_owner_workspace_has_no_grants(owner):
    gui, _ = owner
    assert gui.projects() == []
    assert gui.proposed_changes() == []


def test_friendly_connection_defaults_read_only(owner):
    gui, folder = owner
    grant = gui.connect("My Business Website", str(folder))
    assert grant["access"] == "read"
    assert gui.projects()[0]["root"] == str(folder.resolve())
    assert gui.projects()[0]["name"] == "My Business Website"


def test_write_permission_never_implicit(owner):
    gui, folder = owner
    with pytest.raises(OwnerReviewRequired):
        gui.connect("Business", str(folder), allow_proposals=True)
    assert gui.projects() == []


def test_write_permission_requires_separate_owner_confirmation(owner):
    gui, folder = owner
    grant = gui.connect(
        "Business", str(folder), allow_proposals=True, confirmed_write=True
    )
    assert grant["access"] == "readwrite"
    assert gui.projects()[0]["access"] == "readwrite"


def test_invalid_project_folder_denied(owner):
    gui, folder = owner
    with pytest.raises(AccessDenied):
        gui.connect("Business", str(folder / "does_not_exist"))
    assert gui.projects() == []


def test_disconnect_requires_explicit_owner_approval(owner):
    gui, folder = owner
    project_id = gui.connect("Business", str(folder))["id"]
    with pytest.raises(OwnerReviewRequired):
        gui.disconnect(project_id)
    assert len(gui.projects()) == 1
    assert gui.disconnect(project_id, confirmed=True)
    assert gui.projects() == []


def test_disconnect_rejects_future_reads_and_pending_edits(owner):
    gui, folder = owner
    grant = gui.connect("Business", str(folder), True, True)
    pid = gui.store.propose(grant["id"], "homepage.txt", "AI suggested content")
    assert len(gui.proposed_changes()) == 1
    gui.disconnect(grant["id"], confirmed=True)
    assert gui.proposed_changes() == []
    with pytest.raises(AccessDenied):
        gui.review_change(pid["proposal_id"])


def test_review_change_shows_natural_owner_language(owner):
    gui, folder = owner
    grant = gui.connect("Home Services Website", str(folder), True, True)
    pid = gui.store.propose(grant["id"], "homepage.txt", "Better website\n")
    review = gui.review_change(pid["proposal_id"])
    assert review.project_name == "Home Services Website"
    assert review.relative_path == "homepage.txt"
    assert "Better website" in review.displayed_diff
    assert review.file_existed
    assert len(review.review_token) == 64
    assert len(review.new_sha256) == 64
    assert (folder / "homepage.txt").read_text(encoding="utf-8") == "Original website\n"


def test_review_displays_ansi_and_bidi_as_literal_escapes(owner):
    gui, folder = owner
    grant = gui.connect("Business", str(folder), True, True)
    p = gui.store.propose(
        grant["id"], "homepage.txt", "Friendly text\x1b[2K\rFAKE\x1b[1A\u202eHIDE\n"
    )
    review = gui.review_change(p["proposal_id"])
    assert "\x1b" not in review.displayed_diff
    assert "\r" not in review.displayed_diff
    assert "\u202e" not in review.displayed_diff
    assert r"\u001b" in review.displayed_diff
    assert r"\u202e" in review.displayed_diff
    assert r"\u000d" in review.displayed_diff


def test_safe_display_does_not_escape_regular_prose():
    assert safe_display("Get found. Get chosen.\n") == "Get found. Get chosen.\n"


def test_owner_review_required_before_approving(owner):
    gui, folder = owner
    g = gui.connect("Business", str(folder), True, True)
    pid = gui.store.propose(g["id"], "homepage.txt", "New copy")["proposal_id"]
    review = gui.review_change(pid)
    with pytest.raises(OwnerReviewRequired):
        gui.approve_change(pid, review.review_token)
    assert (folder / "homepage.txt").read_text(encoding="utf-8") == "Original website\n"


def test_invalid_preview_token_prevents_approval(owner):
    gui, folder = owner
    g = gui.connect("Business", str(folder), True, True)
    pid = gui.store.propose(g["id"], "homepage.txt", "New copy")["proposal_id"]
    with pytest.raises(ConflictError):
        gui.approve_change(pid, "unknown-review", confirmed=True)
    assert (folder / "homepage.txt").read_text(encoding="utf-8") == "Original website\n"


def test_reviewed_approved_edit_changes_only_selected_file(owner):
    gui, folder = owner
    (folder / "unrelated.txt").write_text("Unchanged", encoding="utf-8")
    g = gui.connect("Business", str(folder), True, True)
    pid = gui.store.propose(g["id"], "homepage.txt", "Reviewed copy\n")["proposal_id"]
    token = gui.review_change(pid).review_token
    assert gui.approve_change(pid, token, confirmed=True)["applied"]
    assert (folder / "homepage.txt").read_text(encoding="utf-8") == "Reviewed copy\n"
    assert (folder / "unrelated.txt").read_text(encoding="utf-8") == "Unchanged"


def test_stale_owner_review_conflicts_if_source_changes(owner):
    gui, folder = owner
    g = gui.connect("Business", str(folder), True, True)
    pid = gui.store.propose(g["id"], "homepage.txt", "Suggested")["proposal_id"]
    token = gui.review_change(pid).review_token
    (folder / "homepage.txt").write_text("Owner edited it", encoding="utf-8")
    with pytest.raises(ConflictError):
        gui.approve_change(pid, token, confirmed=True)
    assert (folder / "homepage.txt").read_text(encoding="utf-8") == "Owner edited it"


def test_missing_proposal_cannot_be_reviewed(owner):
    gui, _ = owner
    with pytest.raises(AccessDenied):
        gui.review_change("no-such-proposal")


def test_reject_requires_confirmation_and_preserves_original(owner):
    gui, folder = owner
    g = gui.connect("Business", str(folder), True, True)
    pid = gui.store.propose(g["id"], "homepage.txt", "Suggested")["proposal_id"]
    with pytest.raises(OwnerReviewRequired):
        gui.reject_change(pid)
    assert len(gui.proposed_changes()) == 1
    assert gui.reject_change(pid, confirmed=True)
    assert gui.proposed_changes() == []
    assert (folder / "homepage.txt").read_text(encoding="utf-8") == "Original website\n"


def test_owner_activity_does_not_retain_file_body(owner):
    gui, folder = owner
    g = gui.connect("Business", str(folder), True, True)
    gui.store.propose(g["id"], "homepage.txt", "Private preview content")
    assert any(e["action"] == "propose_edit" for e in gui.activity())
    assert "Private preview content" not in str(gui.activity())


def test_native_window_has_no_automatic_connection():
    from app.local_connect_ui import window
    assert callable(window.main)
    source = inspect.getsource(window.ConnectedWorkspaceWindow.choose_folder)
    assert "filedialog.askdirectory" in source
    assert "confirmed_write=writable" in source
    assert "socket" not in source
    assert "subprocess" not in source


def test_double_click_launcher_is_local_only():
    launcher = (
        Path(__file__).resolve().parents[1] / "app" / "local_connect_ui" /
        "launch_connected_workspace.cmd"
    )
    text = launcher.read_text(encoding="utf-8")
    assert "python -m app.local_connect_ui" in text
    assert "dangerously-skip-permissions" not in text
    assert "http://" not in text
    assert "https://" not in text
