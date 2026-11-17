"""Owner-visible MeasuredField account link panel. No credentials enter Tk widgets."""
from __future__ import annotations

import socket
import tkinter as tk
from tkinter import ttk
from typing import Callable

from app.account_link.client import (
    DeviceClientError, configured_workspace, developer_mode_enabled,
)
from app.account_link.secret_store import SecureStoreUnavailable
from app.account_link.controller import AccountLinkController, AccountView
from app.local_connect import AccessDenied


class AccountPanel(ttk.Frame):
    POLL_MS = 15_000

    def __init__(self, parent: ttk.Notebook, controller: AccountLinkController,
                 on_change: Callable[[], None] | None = None):
        super().__init__(parent, padding=14)
        self.controller = controller
        self.on_change = on_change
        # Installation configuration is authoritative. Cached JSON and typed
        # addresses never replace a pinned HTTPS workspace.
        self._configuration_invalid = False
        try:
            self._installed_url = configured_workspace()
        except DeviceClientError:
            # A damaged installer configuration disables pairing with a clear
            # owner-facing explanation instead of aborting the Tk window.
            self._installed_url = None
            self._configuration_invalid = True
        self._dev_mode = (
            developer_mode_enabled() and self._installed_url is None
            and not self._configuration_invalid
        )
        self.url = tk.StringVar(
            value=self._installed_url or (
                self.controller.view().server_url if self._dev_mode else "")
        )
        self.device_name = tk.StringVar(value=socket.gethostname()[:80] or "My computer")
        self.code = tk.StringVar()
        self.state = tk.StringVar()
        self.instructions = tk.StringVar()
        self.number = tk.StringVar()
        ttk.Label(self, text="Connect this computer to your business", style="Subheading.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(self, text="1. In your signed-in MeasuredField workspace, open Computers and create a one-time code.").pack(anchor="w", pady=(0, 9))
        form = ttk.Frame(self)
        form.pack(fill="x")
        ttk.Label(form, text="Trusted MeasuredField workspace").grid(row=0, column=0, sticky="w", pady=4)
        self.url_input = ttk.Entry(form, textvariable=self.url, width=64)
        self.url_input.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)
        self.url_input.configure(
            state="readonly" if self._installed_url else (
                "normal" if self._dev_mode else "disabled")
        )
        ttk.Label(form, text="Name for this computer").grid(row=1, column=0, sticky="w", pady=4)
        self.name_input = ttk.Entry(form, textvariable=self.device_name, width=40)
        self.name_input.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=4)
        ttk.Label(form, text="One-time code").grid(row=2, column=0, sticky="w", pady=4)
        self.code_input = ttk.Entry(form, textvariable=self.code, width=25)
        self.code_input.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=4)
        form.columnconfigure(1, weight=1)

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 8))
        self.connect_button = ttk.Button(actions, text="Connect this computer", command=self.connect)
        self.connect_button.pack(side="left", padx=(0, 8))
        self.check_button = ttk.Button(actions, text="Check confirmation", command=self.check_status)
        self.check_button.pack(side="left", padx=(0, 8))
        self.disconnect_button = ttk.Button(actions, text="Disconnect this computer", command=self.disconnect)
        self.disconnect_button.pack(side="left")
        ttk.Label(self, textvariable=self.state, style="Subheading.TLabel", wraplength=850).pack(anchor="w", pady=(8, 4))
        ttk.Label(self, textvariable=self.number, style="Heading.TLabel").pack(anchor="w", pady=(6, 6))
        ttk.Label(self, textvariable=self.instructions, wraplength=850).pack(anchor="w", pady=(4, 8))
        ttk.Label(self, text="2. Type the six-digit check number shown here into your workspace. "
                  "3. Choose Check confirmation. Folder permissions remain separate.",
                  wraplength=850).pack(anchor="w", pady=(8, 8))
        ttk.Label(self, text="This does not activate a public tunnel, remote control, or a ChatGPT connection.",
                  wraplength=850).pack(anchor="w")
        self.render()
        # Do not trust persisted permissions during the first 15 seconds of a new window.
        self.after(100, self._poll)

    def render(self, view: AccountView | None = None) -> None:
        view = view or self.controller.view()
        if view.server_url:
            self.url.set(view.server_url)
        status = {
            "not_connected": "Not connected to a MeasuredField account",
            "pending": "Awaiting workspace confirmation",
            "active": "Connected to " + (view.business or "your workspace"),
            "unverified": "Workspace connection could not be verified",
            "revoked": "Disconnected",
        }.get(view.status, "Not connected")
        if view.status == "active":
            status += " — " + ("Read only" if view.access == "read" else "May suggest changes with approval")
        self.state.set(status)
        self.number.set(("Check number: " + view.check_number) if view.status == "pending" and view.check_number else "")
        if self._configuration_invalid:
            self.instructions.set(
                "The installed workspace address is invalid. Pairing is disabled. "
                "Contact the MeasuredField installer or administrator.")
        elif not self._installed_url and not self._dev_mode:
            self.instructions.set(
                "Computer linking is unavailable until MeasuredField installs a "
                "trusted workspace address. An address typed by a person is not enough.")
        else:
            self.instructions.set(view.notice)
        if self._installed_url:
            self.url.set(self._installed_url)
        inactive = view.status in ("not_connected", "revoked")
        can_connect = bool(self._installed_url or self._dev_mode)
        self.connect_button.configure(state="normal" if inactive and can_connect else "disabled")
        self.check_button.configure(state="normal" if view.status in ("pending", "active", "unverified") else "disabled")
        self.disconnect_button.configure(state="disabled" if inactive else "normal")

    def _action(self, fn) -> None:
        try:
            view = fn()
            self.render(view)
            if self.on_change is not None:
                self.on_change()
        except (AccessDenied, DeviceClientError, SecureStoreUnavailable, OSError, ValueError):
            # Untrusted server text, codes and credentials must never appear in the UI.
            self.render()
            self.instructions.set("Unable to complete this request. Check the workspace address or code and try again.")

    def connect(self) -> None:
        def run():
            view = self.controller.connect(self.url.get().strip(), self.code.get().strip(),
                                           self.device_name.get().strip())
            self.code.set("")
            return view
        self._action(run)

    def check_status(self) -> None:
        self._action(self.controller.refresh)

    def disconnect(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno("Disconnect this computer?",
                                   "Disconnect from the MeasuredField workspace and revoke local trusted sessions?",
                                   parent=self):
            return
        self._action(self.controller.disconnect)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        if self.controller.view().status in ("pending", "active", "unverified"):
            self.check_status()
        self.after(self.POLL_MS, self._poll)
