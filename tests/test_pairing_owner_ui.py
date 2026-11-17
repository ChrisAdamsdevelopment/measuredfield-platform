"""Synthetic owner-friendly pairing controls; no customer folders or external tunnel."""
from __future__ import annotations

import gc
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from app.local_connect import LocalConnect, AccessDenied
from app.local_connect_ui.pairing_controller import PairingOwnerController


@pytest.fixture
def owner(tmp_path):
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    project_a.mkdir()
    project_b.mkdir()
    (project_a / "page.txt").write_text("Alpha text", encoding="utf-8")
    (project_b / "page.txt").write_text("Beta text", encoding="utf-8")
    store = LocalConnect(tmp_path / "private_state")
    alpha = store.grant("Alpha website", project_a, "readwrite")["id"]
    beta = store.grant("Beta office", project_b, "read")["id"]
    return PairingOwnerController(store), alpha, beta, project_a, project_b



@pytest.fixture(scope="module")
def tk_root():
    """Initialize Windows Tcl/Tk once, fail (never silently skip) on Windows."""
    import tkinter as tk
    from tkinter import ttk

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        # Initialize ttk scripts now rather than during a late GUI test.
        scrollbar = ttk.Scrollbar(root)
        scrollbar.destroy()
        root.update_idletasks()
    except tk.TclError as error:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        if sys.platform == "win32" or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            pytest.fail(f"Native Tk initialization failed: {error}")
        pytest.skip(f"No graphical Tcl/Tk display is available: {error}")
    try:
        yield root
    finally:
        # Collect Tk variable wrappers on this GUI thread while Tcl is live.
        root.update_idletasks()
        gc.collect()
        root.destroy()


@pytest.fixture
def tk_host(tk_root):
    """Each test gets fresh widgets while Tcl/Tk remains alive between tests."""
    try:
        yield tk_root
    finally:
        # Explicitly release StringVar / BooleanVar references on the main
        # GUI thread before worker-thread garbage collection can finalize them.
        children = list(tk_root.winfo_children())
        for child in children:
            child.destroy()
            for name in ("name", "minutes", "suggestions"):
                if hasattr(child, name):
                    setattr(child, name, None)
        # Release references even if a widget constructor failed before adding children.
        child = None
        children.clear()
        tk_root.update_idletasks()
        gc.collect()


def test_owner_can_view_explicit_projects(owner):
    ui, alpha, beta, project_a, project_b = owner
    projects = ui.available_projects()
    assert {p["id"] for p in projects} == {alpha, beta}
    assert {p["root"] for p in projects} == {str(project_a), str(project_b)}
    assert ui.connections() == []


def test_pair_requires_owner_confirmation(owner):
    ui, alpha, *_ = owner
    with pytest.raises(AccessDenied):
        ui.pair("Assistant", [alpha])
    assert ui.connections() == []


def test_default_pair_is_read_only_and_scope_bounded(owner):
    ui, alpha, beta, *_ = owner
    result = ui.pair("Private sample", [alpha], confirmed=True)
    assert result["read_only"] is True
    assert result["project_count"] == 1
    rows = ui.connections()
    assert len(rows) == 1
    assert rows[0]["can_suggest_edits"] is False
    assert "token" not in str(rows)
    assert "secret" not in str(rows)
    assert "root" not in str(rows)
    assert ui.registry.authorize(
        result["pairing_id"],
        ui.registry.token_for_local_launcher(result["pairing_id"]),
        alpha, "read",
    )
    with pytest.raises(AccessDenied):
        ui.registry.authorize(
            result["pairing_id"],
            ui.registry.token_for_local_launcher(result["pairing_id"]),
            beta, "read",
        )


def test_pair_suggestion_permission_requires_separate_consent(owner):
    ui, alpha, *_ = owner
    with pytest.raises(AccessDenied):
        ui.pair("Assistant", [alpha], allow_suggestions=True, confirmed=True)
    assert ui.connections() == []


def test_pair_suggestions_possible_only_for_write_enabled_folder(owner):
    ui, alpha, beta, *_ = owner
    with pytest.raises(AccessDenied):
        ui.pair("No write", [beta], allow_suggestions=True,
                confirmed=True, confirmed_suggestions=True)
    result = ui.pair("Selected write", [alpha, beta], allow_suggestions=True,
                     confirmed=True, confirmed_suggestions=True)
    assert result["read_only"] is False
    token = ui.registry.token_for_local_launcher(result["pairing_id"])
    with pytest.raises(AccessDenied):
        ui.registry.authorize(result["pairing_id"], token, beta, "propose")


@pytest.mark.parametrize("minutes", [-1, 0, True, 1500, "15", 1.4])
def test_pair_refuses_invalid_expiration(owner, minutes):
    ui, alpha, *_ = owner
    with pytest.raises(AccessDenied):
        ui.pair("Bad", [alpha], minutes=minutes, confirmed=True)


def test_pair_requires_existing_grants(owner):
    ui, alpha, *_ = owner
    with pytest.raises(AccessDenied):
        ui.pair("Unknown", ["no-such-id"], confirmed=True)
    with pytest.raises(AccessDenied):
        ui.pair("None", [], confirmed=True)


def test_pair_refuses_duplicate_or_too_many_grants(owner):
    ui, alpha, *_ = owner
    with pytest.raises(AccessDenied):
        ui.pair("Duplicate", [alpha, alpha], confirmed=True)
    with pytest.raises(AccessDenied):
        ui.pair("Long", [alpha] * 9, confirmed=True)


def test_session_disconnection_is_owner_approved(owner):
    ui, alpha, *_ = owner
    pair = ui.pair("Test", [alpha], confirmed=True)
    with pytest.raises(AccessDenied):
        ui.disconnect(pair["pairing_id"])
    assert ui.connections()[0]["status"] == "active"
    assert ui.disconnect(pair["pairing_id"], confirmed=True)
    assert ui.connections()[0]["status"] == "revoked"


def test_owner_can_disconnect_even_when_folder_was_revoked(owner):
    ui, alpha, *_ = owner
    pair = ui.pair("Test", [alpha], confirmed=True)
    ui.store.revoke(alpha)
    assert ui.disconnect(pair["pairing_id"], confirmed=True)
    assert ui.connections()[0]["status"] == "revoked"


def test_expired_pair_is_marked_in_owner_display(owner):
    ui, alpha, *_ = owner
    p = ui.pair("Test", [alpha], minutes=1, confirmed=True)
    with sqlite3.connect(ui.registry.db_path) as db:
        db.execute("UPDATE pairings SET expires_at=? WHERE id=?",
                   (time.time() - 1, p["pairing_id"]))
    assert ui.connections()[0]["status"] == "expired"


def test_pairing_activity_contains_no_raw_credential(owner):
    ui, alpha, *_ = owner
    p = ui.pair("Test", [alpha], confirmed=True)
    secret = ui.registry.token_for_local_launcher(p["pairing_id"])
    assert secret not in str(ui.pairing_activity())
    assert any(row["operation"] == "pair" for row in ui.pairing_activity())


def test_owner_panel_constructs_and_revoke_via_buttons(owner, tk_host, monkeypatch):
    from tkinter import messagebox
    from app.local_connect_ui.pairing_panel import PairingPanel

    ui, alpha, beta, *_ = owner
    approved_prompts = []
    messages = []
    monkeypatch.setattr(messagebox, "askyesno",
                        lambda *args, **kwargs: approved_prompts.append(args) or True)
    monkeypatch.setattr(messagebox, "showinfo",
                        lambda *args, **kwargs: messages.append(args))
    monkeypatch.setattr(messagebox, "showerror",
                        lambda *args, **kwargs: pytest.fail(str(args)))
    panel = PairingPanel(tk_host, ui)
    panel.pack()
    assert set(panel.project_tree.get_children()) == {alpha, beta}
    panel.project_tree.selection_set(alpha)
    panel.name.set("Desk pilot")
    panel.pair()
    assert len(panel.connection_tree.get_children()) == 1
    selected = panel.connection_tree.get_children()[0]
    assert ui.connections()[0]["can_suggest_edits"] is False
    panel.connection_tree.selection_set(selected)
    panel.disconnect()
    assert ui.connections()[0]["status"] == "revoked"
    assert len(approved_prompts) == 2

def test_owner_panel_is_not_network_transport():
    from app.local_connect_ui import pairing_panel
    import inspect
    source = inspect.getsource(pairing_panel.PairingPanel)
    assert "askyesno" in source
    assert "sock.connect(" not in source
    assert "subprocess.Popen" not in source
    assert "tunnel-client" not in source


def test_exact_folder_detail_for_owner_only(owner):
    ui, alpha, beta, folder_a, folder_b = owner
    p = ui.pair("Selected folders", [alpha], confirmed=True)
    details = ui.connection_projects(p["pairing_id"])
    assert details == [{
        "id": alpha,
        "name": "Alpha website",
        "root": str(folder_a),
        "access": "Read only",
    }]
    assert str(folder_b) not in str(details)
    assert str(folder_a) not in str(ui.connections())


def test_owner_detail_shows_disconnected_grants_as_inaccessible(owner):
    ui, alpha, *_ = owner
    pair = ui.pair("Soon disconnected", [alpha], confirmed=True)
    ui.store.revoke(alpha)
    detail = ui.connection_projects(pair["pairing_id"])
    assert detail[0]["name"] == "Disconnected folder"
    assert detail[0]["access"] == "Disconnected"
    assert detail[0]["root"] == "(No longer accessible)"


def test_owner_detail_rejects_unknown_connection(owner):
    ui, *_ = owner
    with pytest.raises(AccessDenied):
        ui.connection_projects("not-a-connection")


def test_owner_pair_refuses_blank_label_before_creation(owner):
    ui, alpha, *_ = owner
    with pytest.raises(AccessDenied):
        ui.pair("  ", [alpha], confirmed=True)
    assert ui.connections() == []


def test_panel_rejects_blank_name_before_confirmation(owner, tk_host, monkeypatch):
    from tkinter import messagebox
    from app.local_connect_ui.pairing_panel import PairingPanel
    ui, alpha, *_ = owner
    dialogs = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **kw: dialogs.append("ask") or True)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **kw: dialogs.append("error"))
    panel = PairingPanel(tk_host, ui)
    panel.project_tree.selection_set(alpha)
    panel.name.set(" ")
    panel.pair()
    assert dialogs == ["error"]
    assert not ui.connections()

def test_owner_can_inspect_exact_folder_details_and_activity(owner, tk_host, monkeypatch):
    import tkinter as tk
    from tkinter import messagebox
    from app.local_connect_ui.pairing_panel import PairingPanel
    ui, alpha, beta, folder_a, folder_b = owner
    pair = ui.pair("Only this folder", [alpha], confirmed=True)
    dialogs = []
    monkeypatch.setattr(messagebox, "showinfo",
                        lambda *args, **kw: dialogs.append(args))
    panel = PairingPanel(tk_host, ui)
    rows = panel.connection_tree.get_children()
    assert len(rows) == 1
    values = panel.connection_tree.item(rows[0])["values"]
    assert "Alpha website" in str(values)
    assert "Beta office" not in str(values)
    panel.connection_tree.selection_set(pair["pairing_id"])
    panel.view_connection()
    assert str(folder_a) in dialogs[0][1]
    assert str(folder_b) not in dialogs[0][1]
    panel.view_activity()
    popups = [child for child in panel.winfo_children()
              if isinstance(child, tk.Toplevel)]
    assert len(popups) == 1
    popups[0].destroy()

def test_disconnecting_inactive_row_explains_status(owner, tk_host, monkeypatch):
    from tkinter import messagebox
    from app.local_connect_ui.pairing_panel import PairingPanel
    ui, alpha, *_ = owner
    pair = ui.pair("Already gone", [alpha], confirmed=True)
    ui.disconnect(pair["pairing_id"], confirmed=True)
    notices = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *args, **kw: notices.append(args))
    panel = PairingPanel(tk_host, ui)
    panel.connection_tree.selection_set(pair["pairing_id"])
    panel.disconnect()
    assert any("inactive" in str(note).lower() for note in notices)
    assert ui.connections()[0]["status"] == "revoked"
