"""Local-only, plain-language trusted connection panel for Windows owners."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from app.local_connect import AccessDenied
from app.account_link.runtime_guard import AccountRuntimeGuard
from app.desktop_companion.worker_launcher import launch_owner_work_session
from .controller import safe_display
from .pairing_controller import PairingOwnerController

EXPIRE_OPTIONS = ("5", "15", "30", "60", "120", "480", "1440")


class PairingPanel(ttk.Frame):
    """Shows owner-granted folders and expiring pairings; never starts a tunnel."""

    def __init__(self, master, owner: PairingOwnerController,
                 report: Callable[[str], None] | None = None):
        super().__init__(master, padding=12)
        self.owner = owner
        self._workers = {}
        self.report = report or (lambda _msg: None)
        ttk.Label(
            self, text="Connect a trusted assistant to specific project folders",
            style="Subheading.TLabel",
        ).pack(anchor="w", pady=(0, 6))
        ttk.Label(
            self, text="Authorize the folders you choose. Verified Work mode connects to your MeasuredField workspace over outbound HTTPS.",
            style="Body.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(self, text="1. Choose the folders this temporary connection may see.").pack(
            anchor="w", pady=(6, 4)
        )
        self.project_tree = ttk.Treeview(
            self, columns=("name", "access", "folder"), show="headings",
            selectmode="extended", height=5,
        )
        for col, title, width in (
            ("name", "Project", 170),
            ("access", "Permission", 160),
            ("folder", "Folder on computer", 440),
        ):
            self.project_tree.heading(col, text=title)
            self.project_tree.column(col, width=width, minwidth=100, stretch=True)
        self.project_tree.pack(fill="x")
        ttk.Label(
            self, text="Select one or more rows (Ctrl-click for multiple folders). "
                       "Other connected folders stay private."
        ).pack(anchor="w", pady=(3, 9))

        form = ttk.Frame(self)
        form.pack(fill="x", pady=(0, 6))
        ttk.Label(form, text="2. Connection name").grid(row=0, column=0, sticky="w")
        self.name = tk.StringVar(value="Temporary project Work")
        ttk.Entry(form, textvariable=self.name, width=28).grid(
            row=1, column=0, sticky="ew", padx=(0, 12)
        )
        ttk.Label(form, text="3. Disconnect automatically after").grid(
            row=0, column=1, sticky="w"
        )
        self.minutes = tk.StringVar(value="15")
        self.duration = ttk.Combobox(
            form, values=EXPIRE_OPTIONS, textvariable=self.minutes,
            state="readonly", width=12,
        )
        self.duration.grid(row=1, column=1, sticky="w")
        ttk.Label(form, text="minutes").grid(row=1, column=2, sticky="w", padx=(5, 0))
        form.columnconfigure(0, weight=1)

        self.suggestions = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self, text="Allow the assistant to suggest edits (you still approve each one)",
            variable=self.suggestions,
        ).pack(anchor="w", pady=(6, 7))
        self.work_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self, text="Work mode: directly create and edit files during this session",
            variable=self.work_mode,
        ).pack(anchor="w", pady=(0, 7))
        # These are mutually exclusive modes, not two overlapping grants.
        self.suggestions.trace_add(
            "write", lambda *_: self.work_mode.set(False)
            if self.suggestions.get() and self.work_mode.get() else None,
        )
        self.work_mode.trace_add(
            "write", lambda *_: self.suggestions.set(False)
            if self.work_mode.get() and self.suggestions.get() else None,
        )
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 9))
        ttk.Button(
            actions, text="Pair selected folders", command=self.pair,
        ).pack(side="left", padx=(0, 12))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(5, 10))
        ttk.Label(
            self, text="Trusted connections on this computer",
            style="Subheading.TLabel",
        ).pack(anchor="w", pady=(0, 6))
        self.connection_tree = ttk.Treeview(
            self, columns=("name", "status", "folders", "permission", "expires"),
            show="headings", selectmode="browse", height=5,
        )
        for col, label, width in (
            ("name", "Name", 185),
            ("status", "Status", 90),
            ("folders", "Approved folders", 235),
            ("permission", "Access", 160),
            ("expires", "Expires (UTC)", 230),
        ):
            self.connection_tree.heading(col, text=label)
            self.connection_tree.column(col, width=width, stretch=True, minwidth=75)
        self.connection_tree.pack(fill="both", expand=True)
        connection_actions = ttk.Frame(self)
        connection_actions.pack(anchor="w", pady=(10, 4))
        ttk.Button(
            connection_actions, text="View selected connection", command=self.view_connection,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            connection_actions, text="Disconnect selected connection", command=self.disconnect,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            connection_actions, text="Connection activity", command=self.view_activity,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            connection_actions, text="Start cloud Work", command=self.start_cloud_work,
        ).pack(side="left")
        ttk.Label(
            self, text="Only explicitly approved Work-mode pairings can change project files. "
                       "No shell, delete, publish, or network tunnel starts automatically. "
                       "If online verification fails, sessions stop and require reapproval.",
        ).pack(anchor="w", pady=(6, 0))
        self.refresh()

    def _error(self, error: Exception) -> None:
        self.report("Pairing action could not be completed.")
        messagebox.showerror(
            "Connection not changed", safe_display(str(error)), parent=self.winfo_toplevel(),
        )

    def pair(self) -> None:
        project_ids = list(self.project_tree.selection())
        if not project_ids:
            messagebox.showinfo(
                "Choose folders", "Select at least one project folder first.",
                parent=self.winfo_toplevel()
            )
            return
        try:
            name = self.name.get().strip()
            if not name:
                raise AccessDenied("Choose a name for this connection")
            minutes = int(self.minutes.get())
            writable = bool(self.suggestions.get())
            direct_work = bool(self.work_mode.get())
            if direct_work and minutes > 480:
                raise AccessDenied("Work mode expires within eight hours; choose a shorter session.")
            details = {item["id"]: item for item in self.owner.available_projects()}
            if any(pid not in details for pid in project_ids):
                raise AccessDenied("A selected folder was disconnected; refresh and choose again")
            if (writable or direct_work) and not any(
                    details[pid]["access"] == "readwrite" for pid in project_ids):
                raise AccessDenied(
                    "Enable readwrite access to an approved folder before allowing edits"
                )
            selected = "\n".join(
                "- " + safe_display(details[pid]["name"]) +
                " (" + ("Direct Work edits" if direct_work and details[pid]["access"] == "readwrite"
                        else "Suggest edits" if writable and details[pid]["access"] == "readwrite"
                        else "Read only") + "): " +
                safe_display(details[pid]["root"])
                for pid in project_ids
            )
            if not messagebox.askyesno(
                "Pair only these folders?",
                "Authorize this scoped connection?\n\n" +
                "Connection: " + safe_display(name) + "\n" +
                "Duration: " + str(minutes) + " minutes\n\n" +
                selected + "\n\nCloud Work mode starts only for a live, verified Work-mode device. ""No inbound listener is opened.",
                parent=self.winfo_toplevel(),
            ):
                return
            if writable and not direct_work and not messagebox.askyesno(
                "Confirm suggested changes",
                "Allow suggested edits inside the write-enabled folders listed?\n\n"
                "Every actual file change still needs your separate approval.",
                parent=self.winfo_toplevel(),
            ):
                return
            if direct_work:
                if not messagebox.askyesno(
                    "Authorize real file changes?",
                    "Work mode can CREATE and MODIFY actual files in the approved folders "
                    "without asking again for every file. It cannot delete files, run shell "
                    "commands, or publish websites. You can disconnect this session any time. "
                    "Do you permit this direct access for the selected duration?",
                    parent=self.winfo_toplevel(),
                ):
                    return
            if direct_work:
                # Check CURRENT cloud authorization before creating a new
                # writable session. An offline/stale device is never promoted.
                AccountRuntimeGuard(self.owner.store.home).check(action="write")
            pair = self.owner.pair(
                name, project_ids, minutes=minutes,
                allow_suggestions=writable and not direct_work, allow_writes=direct_work,
                confirmed=True, confirmed_suggestions=writable and not direct_work,
                confirmed_writes=direct_work,
            )
            if direct_work:
                try:
                    self._workers[pair["pairing_id"]] = launch_owner_work_session(
                        pair["pairing_id"], registry=self.owner.registry)
                except (AccessDenied, OSError):
                    self.owner.disconnect(pair["pairing_id"], confirmed=True)
                    raise
            self.suggestions.set(False)
            self.work_mode.set(False)
            self.refresh()
            self.report(
                "Owner-approved cloud Work worker launched." if direct_work else
                "Private local connection created."
            )
            messagebox.showinfo(
                "Work session launched" if direct_work else "Local connection created",
                ("The Work worker was launched for your approved folders. "
                 "Actual work requires your connected ChatGPT account and a valid "
                 "device token; check the Computers page for completed receipts."
                 if direct_work else
                 "This local pairing is ready for an authorized local session. ")
                + "\n\nExpires at " + pair["expires_at"] + ".",
                parent=self.winfo_toplevel(),
            )
        except (AccessDenied, ValueError, OSError) as exc:
            self._error(exc)

    def start_cloud_work(self) -> None:
        """Explicit owner UI action; no model/API caller can start local work."""
        selection = self.connection_tree.selection()
        if not selection:
            messagebox.showinfo(
                "Select a Work session",
                "Select an active Work-mode connection first.",
                parent=self.winfo_toplevel(),
            )
            return
        pair_id = selection[0]
        try:
            pair = next(
                (c for c in self.owner.connections() if c["pairing_id"] == pair_id),
                None,
            )
            if not pair or pair["status"] != "active" or not pair.get("can_write"):
                raise AccessDenied("Select an active owner-approved Work connection")
            existing = self._workers.get(pair_id)
            if existing is not None and existing.poll() is None:
                messagebox.showinfo(
                    "Work session already started",
                    "The worker is already running for this connection.",
                    parent=self.winfo_toplevel(),
                )
                return
            process = launch_owner_work_session(
                pair_id, registry=self.owner.registry)
            self._workers[pair_id] = process
            self.report("Cloud Work worker process started.")
            messagebox.showinfo(
                "Work session started",
                "The background worker started. It executes only scoped jobs "
                "while your account and this timed pairing remain authorized.",
                parent=self.winfo_toplevel(),
            )
        except (AccessDenied, OSError, ValueError) as exc:
            self._error(exc)

    def view_connection(self) -> None:
        """Owner-only exact folder list; no bearer secrets or AI-visible output."""
        selection = self.connection_tree.selection()
        if not selection:
            messagebox.showinfo(
                "Select a connection", "Choose a connection to inspect.",
                parent=self.winfo_toplevel(),
            )
            return
        try:
            pair = next(
                (c for c in self.owner.connections() if c["pairing_id"] == selection[0]),
                None,
            )
            if not pair:
                raise AccessDenied("Connection not found")
            selected = self.owner.connection_projects(pair["pairing_id"])
            lines = []
            for item in selected:
                lines.append(
                    "- " + safe_display(item["name"]) +
                    " [" + item["access"] + "]\n" + safe_display(item["root"])
                )
            messagebox.showinfo(
                "Exact folders for " + safe_display(pair["name"]),
                "Status: " + pair["status"] + "\n"
                "Expires (UTC): " + pair["expires_at"] + "\n\n"
                "Only these project folders were selected:\n\n" +
                ("\n\n".join(lines) if lines else "(None)"),
                parent=self.winfo_toplevel(),
            )
        except (AccessDenied, OSError, ValueError) as exc:
            self._error(exc)

    def view_activity(self) -> None:
        """Show pairing/revocation and denied access metadata without file contents."""
        try:
            rows = self.owner.pairing_activity()
            dialog = tk.Toplevel(self)
            dialog.title("Trusted connection activity")
            dialog.geometry("800x420")
            ttk.Label(
                dialog,
                text="Local connection activity (no file contents or secret keys)",
            ).pack(anchor="w", padx=12, pady=(12, 8))
            table = ttk.Treeview(
                dialog, columns=("time", "action", "result", "connection"),
                show="headings",
            )
            for column, title, width in (
                ("time", "When (UTC)", 190),
                ("action", "Action", 130),
                ("result", "Result", 155),
                ("connection", "Connection ID", 280),
            ):
                table.heading(column, text=title)
                table.column(column, width=width, minwidth=90, stretch=True)
            table.pack(fill="both", expand=True, padx=12, pady=(0, 12))
            for row in rows:
                table.insert(
                    "", "end", values=(
                        row["timestamp"], row["operation"], row["outcome"],
                        row["pairing_id"] or "(unknown)",
                    ),
                )
            ttk.Button(dialog, text="Close", command=dialog.destroy).pack(
                anchor="e", padx=12, pady=(0, 12)
            )
            dialog.transient(self.winfo_toplevel())
            dialog.focus_set()
        except (AccessDenied, OSError, ValueError) as exc:
            self._error(exc)

    def disconnect(self) -> None:
        selection = self.connection_tree.selection()
        if not selection:
            messagebox.showinfo(
                "Select a connection", "Choose a connection to disconnect.",
                parent=self.winfo_toplevel()
            )
            return
        pair_id = selection[0]
        try:
            pair = next(
                (p for p in self.owner.connections() if p["pairing_id"] == pair_id), None
            )
            if not pair:
                raise AccessDenied("Connection not found")
            if pair["status"] != "active":
                self.refresh()
                self.report("This connection is already " + pair["status"] + ".")
                messagebox.showinfo(
                    "Connection already inactive",
                    "This connection is already " + pair["status"] + ". No access is active.",
                    parent=self.winfo_toplevel(),
                )
                return
            if not messagebox.askyesno(
                "Disconnect this connection?",
                "Disconnect " + safe_display(pair["name"]) + " now?\n\n"
                "Its next work request will be denied. Your project folders stay on your computer.",
                parent=self.winfo_toplevel(),
            ):
                return
            self.owner.disconnect(pair_id, confirmed=True)
            running = self._workers.pop(pair_id, None)
            if running is not None and running.poll() is None:
                try:
                    running.terminate()
                except OSError:
                    pass  # Local pairing revocation itself denies every future write.
            self.refresh()
            self.report("Trusted local connection revoked.")
        except (AccessDenied, ValueError, OSError) as exc:
            self._error(exc)

    def refresh(self) -> None:
        previous = set(self.project_tree.selection())
        for tree in (self.project_tree, self.connection_tree):
            for row in tree.get_children():
                tree.delete(row)
        for item in self.owner.available_projects():
            self.project_tree.insert(
                "", "end", iid=item["id"],
                values=(
                    safe_display(item["name"]),
                    "Suggest edits + read" if item["access"] == "readwrite" else "Read only",
                    safe_display(item["root"]),
                )
            )
            if item["id"] in previous:
                self.project_tree.selection_add(item["id"])
        for pair in self.owner.connections():
            self.connection_tree.insert(
                "", "end", iid=pair["pairing_id"],
                values=(
                    safe_display(pair["name"]),
                    pair["status"],
                    safe_display(", ".join(
                        item["name"] for item in self.owner.connection_projects(pair["pairing_id"])
                    )),
                    "Direct Work mode" if pair.get("can_write") else
                    "Suggest edits" if pair["can_suggest_edits"] else "Read only",
                    pair["expires_at"],
                ),
            )
