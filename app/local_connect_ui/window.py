"""Windows owner-facing Connected Workspace pilot.

Local-only Tkinter GUI. No HTTP listener, background agent or automatic ChatGPT
connection. Owner actions never become MCP tools.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Callable

from app.local_connect import AccessDenied, ConflictError
from .controller import ChangeReview, OwnerWorkspaceController, safe_display


class ConnectedWorkspaceWindow:
    def __init__(self, root: tk.Tk,
                 controller: OwnerWorkspaceController | None = None) -> None:
        self.root = root
        self.controller = controller or OwnerWorkspaceController()
        self.reviewed: ChangeReview | None = None
        root.title("MeasuredField | Connected Workspace")
        root.geometry("1060x735")
        root.minsize(850, 565)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Heading.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Subheading.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Body.TLabel", font=("Segoe UI", 10))
        style.configure("Danger.TLabel", font=("Segoe UI", 9))
        style.configure("TButton", padding=(12, 8))

        shell = ttk.Frame(root, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Connected Workspace", style="Heading.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            shell,
            text="Choose what MeasuredField can see. Review every suggested file change before it is applied.",
            style="Body.TLabel",
        ).pack(anchor="w", pady=(5, 14))

        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        self.connections_tab = ttk.Frame(self.tabs, padding=12)
        self.proposals_tab = ttk.Frame(self.tabs, padding=12)
        self.activity_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.connections_tab, text="My connected folders")
        self.tabs.add(self.proposals_tab, text="Review changes")
        self.tabs.add(self.activity_tab, text="Activity history")

        self._connections_ui()
        self._proposals_ui()
        self._activity_ui()
        ttk.Separator(shell, orient="horizontal").pack(fill="x", pady=(12, 8))
        ttk.Label(
            shell,
            text="Local pilot only. This window does not connect to your ChatGPT account, "
                 "publish websites, or run in the background.",
            style="Danger.TLabel",
        ).pack(anchor="w")
        self.status = tk.StringVar(value="No folder is shared unless you connect it.")
        ttk.Label(shell, textvariable=self.status, style="Body.TLabel").pack(
            anchor="w", pady=(6, 0)
        )
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self.refresh())
        self.refresh()

    def _connections_ui(self) -> None:
        tab = self.connections_tab
        ttk.Label(
            tab, text="Only the folders you choose are available to the AI.",
            style="Subheading.TLabel"
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            tab, text="Read-only is the default. Protected files and passwords are never shared."
        ).pack(anchor="w", pady=(0, 10))
        cols = ("project", "permission", "folder")
        self.projects_tree = ttk.Treeview(
            tab, columns=cols, show="headings", selectmode="browse", height=12
        )
        for name, heading, width in (
            ("project", "Project name", 190),
            ("permission", "Access", 205),
            ("folder", "Folder on this computer", 470),
        ):
            self.projects_tree.heading(name, text=heading)
            self.projects_tree.column(name, width=width, minwidth=130, stretch=True)
        self.projects_tree.pack(fill="both", expand=True)

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=(14, 6))
        self.allow_proposals = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            actions, variable=self.allow_proposals,
            text="Let AI suggest file edits (I must approve every change)"
        ).pack(anchor="w", pady=(0, 10))
        button_bar = ttk.Frame(actions)
        button_bar.pack(fill="x")
        ttk.Button(
            button_bar, text="Choose a folder to connect",
            command=self.choose_folder
        ).pack(side="left", padx=(0, 10))
        ttk.Button(
            button_bar, text="Disconnect selected folder",
            command=self.disconnect_folder
        ).pack(side="left", padx=(0, 10))
        ttk.Button(button_bar, text="Refresh", command=self.refresh).pack(side="left")

    def _proposals_ui(self) -> None:
        tab = self.proposals_tab
        ttk.Label(
            tab, text="File changes suggested by an AI require your approval.",
            style="Subheading.TLabel"
        ).pack(anchor="w", pady=(0, 8))
        cols = ("project", "file", "kind", "date")
        self.proposals_tree = ttk.Treeview(
            tab, columns=cols, show="headings", selectmode="browse", height=6
        )
        for key, heading, width in (
            ("project", "Project", 165),
            ("file", "File to change", 420),
            ("kind", "Type", 100),
            ("date", "Submitted", 195),
        ):
            self.proposals_tree.heading(key, text=heading)
            self.proposals_tree.column(key, width=width, stretch=True, minwidth=90)
        self.proposals_tree.pack(fill="x")
        self.proposals_tree.bind(
            "<<TreeviewSelect>>", lambda _: self._on_proposal_selection()
        )

        ttk.Label(
            tab, text="Review the differences below before approving.",
            style="Body.TLabel"
        ).pack(anchor="w", pady=(10, 5))
        self.change_details = tk.StringVar(value="Choose a proposed change.")
        ttk.Label(tab, textvariable=self.change_details).pack(anchor="w")
        self.change_text = scrolledtext.ScrolledText(
            tab, height=15, wrap="none", font=("Consolas", 10), state="disabled"
        )
        self.change_text.pack(fill="both", expand=True, pady=(4, 8))
        controls = ttk.Frame(tab)
        controls.pack(fill="x")
        ttk.Button(
            controls, text="Review selected change", command=self.review_selected
        ).pack(side="left", padx=(0, 10))
        ttk.Button(
            controls, text="Approve this change", command=self.approve_selected
        ).pack(side="left", padx=(0, 10))
        ttk.Button(
            controls, text="Reject this change", command=self.reject_selected
        ).pack(side="left", padx=(0, 10))
        ttk.Button(controls, text="Refresh", command=self.refresh).pack(side="left")

    def _activity_ui(self) -> None:
        tab = self.activity_tab
        ttk.Label(
            tab, text="A history of connection activity. File contents are not shown here.",
            style="Subheading.TLabel"
        ).pack(anchor="w", pady=(0, 8))
        cols = ("when", "action", "result", "file")
        self.activity_tree = ttk.Treeview(
            tab, columns=cols, show="headings", selectmode="browse", height=16
        )
        for key, title, width in (
            ("when", "When", 205),
            ("action", "Action", 150),
            ("result", "Result", 185),
            ("file", "File (if applicable)", 390),
        ):
            self.activity_tree.heading(key, text=title)
            self.activity_tree.column(key, width=width, stretch=True)
        self.activity_tree.pack(fill="both", expand=True)
        ttk.Button(tab, text="Refresh activity", command=self.refresh).pack(
            anchor="w", pady=(12, 0)
        )

    def _catch(self, work: Callable[[], None]) -> None:
        try:
            work()
        except (AccessDenied, ConflictError, OSError, ValueError) as exc:
            self.status.set("Action could not be completed.")
            messagebox.showerror(
                "Unable to complete this action", safe_display(str(exc)),
                parent=self.root
            )

    def choose_folder(self) -> None:
        def action() -> None:
            folder = filedialog.askdirectory(
                parent=self.root,
                mustexist=True,
                title="Choose one project folder"
            )
            if not folder:
                return
            import pathlib
            suggested = pathlib.Path(folder).name or "My project"
            name = simpledialog.askstring(
                "Name this project", "What should we call this connected project?",
                initialvalue=suggested, parent=self.root
            )
            if not name or not name.strip():
                return
            writable = bool(self.allow_proposals.get())
            if writable:
                yes = messagebox.askyesno(
                    "Allow suggested edits?",
                    "Allow the AI to PROPOSE edits in this project folder?\n\n"
                    "No edits can be applied until you review and approve each one "
                    "in this window. This does NOT give permission to publish or run commands.",
                    parent=self.root
                )
                if not yes:
                    self.status.set("Connection canceled. No permission was granted.")
                    return
            project = self.controller.connect(
                name.strip(), folder, writable, confirmed_write=writable
            )
            self.allow_proposals.set(False)
            self.refresh()
            self.status.set("Connected: " + safe_display(project["name"]))
        self._catch(action)

    def disconnect_folder(self) -> None:
        def action() -> None:
            selected = self.projects_tree.selection()
            if not selected:
                messagebox.showinfo(
                    "Choose a project", "Select a folder to disconnect first.",
                    parent=self.root
                )
                return
            project_id = selected[0]
            row = next(
                (item for item in self.controller.projects() if item["id"] == project_id),
                None
            )
            if not row:
                raise AccessDenied("That folder is no longer connected")
            if not messagebox.askyesno(
                "Disconnect this folder?",
                "Disconnect " + safe_display(row["name"]) + "?\n\n"
                "The AI will lose access, and all pending changes for this folder "
                "will be canceled.",
                parent=self.root
            ):
                return
            self.controller.disconnect(project_id, confirmed=True)
            self.reviewed = None
            self.refresh()
            self.status.set("Folder disconnected. Its pending edits were canceled.")
        self._catch(action)

    def _selected_proposal_id(self) -> str | None:
        rows = self.proposals_tree.selection()
        return rows[0] if rows else None

    def _set_diff(self, message: str) -> None:
        self.change_text.configure(state="normal")
        self.change_text.delete("1.0", "end")
        self.change_text.insert("1.0", message)
        self.change_text.configure(state="disabled")

    def _on_proposal_selection(self) -> None:
        self.reviewed = None
        self.change_details.set("Select Review to see this proposal.")
        self._set_diff("")

    def review_selected(self) -> None:
        def action() -> None:
            proposal_id = self._selected_proposal_id()
            if not proposal_id:
                messagebox.showinfo(
                    "Choose a change", "Select a proposed file change first.",
                    parent=self.root
                )
                return
            self.reviewed = self.controller.review_change(proposal_id)
            item = self.reviewed
            verb = "Replace" if item.file_existed else "Create"
            self.change_details.set(
                verb + " " + safe_display(item.project_name) + " / " +
                safe_display(item.relative_path) +
                " | Previous hash: " + str(item.old_sha256 or "new file")[:12] +
                " | Proposed hash: " + item.new_sha256[:12]
            )
            self._set_diff(item.displayed_diff)
            self.status.set("Review complete. Approval still requires your confirmation.")
        self._catch(action)

    def approve_selected(self) -> None:
        def action() -> None:
            proposal_id = self._selected_proposal_id()
            if not proposal_id or not self.reviewed or self.reviewed.proposal_id != proposal_id:
                messagebox.showinfo(
                    "Review required",
                    "Select the proposal and click Review selected change first.",
                    parent=self.root
                )
                return
            reviewed = self.reviewed
            if not messagebox.askyesno(
                "Apply this file change?",
                "Apply the exact change you reviewed to:\n"
                + safe_display(reviewed.project_name) + " / "
                + safe_display(reviewed.relative_path) +
                "\n\nThis changes a file on your computer. It will NOT deploy a website.",
                parent=self.root
            ):
                return
            self.controller.approve_change(
                proposal_id, reviewed.review_token, confirmed=True
            )
            self.reviewed = None
            self.refresh()
            self.status.set("Approved change was applied to the local project file.")
        self._catch(action)

    def reject_selected(self) -> None:
        def action() -> None:
            proposal_id = self._selected_proposal_id()
            if not proposal_id:
                messagebox.showinfo(
                    "Choose a change", "Select a proposed change first.",
                    parent=self.root
                )
                return
            proposal = next(
                (p for p in self.controller.proposed_changes()
                 if p["proposal_id"] == proposal_id), None
            )
            if not proposal:
                raise AccessDenied("This proposed change is no longer pending")
            if not messagebox.askyesno(
                "Reject proposed change?",
                "Reject the suggested change to:\n"
                + safe_display(proposal["project"]) + " / "
                + safe_display(proposal["path"])
                + "\n\nThe project file will not be modified.",
                parent=self.root
            ):
                return
            self.controller.reject_change(proposal_id, confirmed=True)
            self.reviewed = None
            self.refresh()
            self.status.set("Proposed change rejected. No project file was modified.")
        self._catch(action)

    def refresh(self) -> None:
        def action() -> None:
            # Retain an approval preview only until a refresh or a new selection.
            self.reviewed = None
            self.change_details.set("Select a proposed change to review.")
            self._set_diff("")
            for tree in (self.projects_tree, self.proposals_tree, self.activity_tree):
                for item in tree.get_children():
                    tree.delete(item)
            for item in self.controller.projects():
                mode = (
                    "Read + suggest edits"
                    if item["access"] == "readwrite" else "Read only"
                )
                self.projects_tree.insert(
                    "", "end", iid=item["id"],
                    values=(safe_display(item["name"]), mode, safe_display(item["root"]))
                )
            for item in self.controller.proposed_changes():
                self.proposals_tree.insert(
                    "", "end", iid=item["proposal_id"],
                    values=(
                        safe_display(item["project"]), safe_display(item["path"]),
                        "Replace" if item["file_existed"] else "New file",
                        item["created_at"],
                    )
                )
            for index, item in enumerate(self.controller.activity()):
                self.activity_tree.insert(
                    "", "end", iid=str(index),
                    values=(
                        item["at"], item["action"], item["outcome"],
                        safe_display(item["relative_path"] or "")
                    )
                )
            count = len(self.controller.projects())
            self.status.set(
                f"{count} project folder{'s' if count != 1 else ''} connected."
            )
        self._catch(action)


def main() -> None:
    root = tk.Tk()
    ConnectedWorkspaceWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
