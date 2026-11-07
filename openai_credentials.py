"""OpenAI Platform credentials stored in Windows Credential Manager.

Never persist keys to Q-Tracker JSON state, source, or logs.  Only user-entered
credentials are stored; no ChatGPT/Codex token extraction and no browser cookie
reading.
"""

from __future__ import annotations

import os
from typing import Optional


SERVICE = "Q-Tracker OpenAI Platform"
ADMIN_ACCOUNT = "admin_api_key"
LEGACY_ACCOUNT = "legacy_api_key"

ADMIN_ENV = "OPENAI_ADMIN_KEY"
LEGACY_ENV = "QTRACKER_OPENAI_LEGACY_KEY"


def get_key(account: str, env_name: Optional[str] = None) -> Optional[str]:
    """Return a credential, preferring the environment over the keyring.

    Environment variables are read at call time so a changed shell value takes
    effect without restarting Q-Tracker.
    """
    if env_name:
        explicit = os.environ.get(env_name, "").strip()
        if explicit:
            return explicit
    try:
        import keyring

        stored = keyring.get_password(SERVICE, account)
    except Exception:
        return None
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return None


def get_admin_key() -> Optional[str]:
    return get_key(ADMIN_ACCOUNT, ADMIN_ENV)


def get_legacy_key() -> Optional[str]:
    return get_key(LEGACY_ACCOUNT, LEGACY_ENV)


def set_key(account: str, value: str) -> None:
    """Write one credential to Windows Credential Manager."""
    import keyring

    keyring.set_password(SERVICE, account, value)


def configure_gui(parent=None) -> bool:
    """Interactive, masked GUI credential entry. Requires user action.

    Returns ``True`` when at least one credential was written, so the caller can
    start a refresh right after the dialog closes.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        return False

    try:
        import keyring
        from keyring import errors as keyring_errors
    except ImportError:
        keyring = None
        keyring_errors = None

    def read(account: str) -> str:
        if keyring is None:
            return ""
        try:
            return keyring.get_password(SERVICE, account) or ""
        except Exception:
            return ""

    def masked(value: str) -> str:
        if not value:
            return ""
        tail = value[-4:]
        return "*" * max(6, len(value) - 4) + tail

    root = tk.Tk() if parent is None else tk.Toplevel(parent)
    root.title("Q-Tracker - OpenAI Platform")
    root.configure(bg="#171B22")
    root.geometry("570x305")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    if parent is None:
        root.withdraw()

    saved = {"changed": False}

    if keyring is None:
        tk.Label(
            root,
            text="Windows keyring is not installed.",
            bg="#171B22",
            fg="#E3E5EA",
            font=("Segoe UI", 11),
            padx=24,
            pady=24,
        ).pack()
        root.deiconify()
        root.mainloop()
        return False

    frame = tk.Frame(root, bg="#171B22", padx=22, pady=18)
    frame.pack(fill="both", expand=True)

    tk.Label(
        frame,
        text="OpenAI Platform Credentials",
        bg="#171B22",
        fg="#E3E5EA",
        font=("Segoe UI", 15, "bold"),
        anchor="w",
    ).pack(fill="x")
    tk.Label(
        frame,
        text="Stored in Windows Credential Manager, not Q-Tracker configuration.",
        bg="#171B22",
        fg="#B9BEC8",
        font=("Segoe UI", 9),
        anchor="w",
        wraplength=520,
    ).pack(fill="x", pady=(2, 12))

    tk.Label(
        frame,
        text="Organization Admin API Key (official usage and costs)",
        bg="#171B22",
        fg="#E3E5EA",
        font=("Segoe UI", 10),
        anchor="w",
    ).pack(fill="x")
    admin = tk.Entry(frame, width=64, show="", bg="#20242C", fg="#E3E5EA", insertbackground="#E3E5EA")
    admin.insert(0, masked(read(ADMIN_ACCOUNT)))
    admin.pack(fill="x", pady=(2, 8))

    tk.Label(
        frame,
        text="Legacy API Key (optional, unsupported credit-grants fallback)",
        bg="#171B22",
        fg="#E3E5EA",
        font=("Segoe UI", 10),
        anchor="w",
    ).pack(fill="x")
    legacy = tk.Entry(frame, width=64, show="", bg="#20242C", fg="#E3E5EA", insertbackground="#E3E5EA")
    legacy.insert(0, masked(read(LEGACY_ACCOUNT)))
    legacy.pack(fill="x", pady=(2, 4))

    show = tk.BooleanVar(master=root, value=False)

    def toggle_show() -> None:
        character = "" if show.get() else "*"
        for entry, account in ((admin, ADMIN_ACCOUNT), (legacy, LEGACY_ACCOUNT)):
            current = entry.get()
            if current and current.startswith("*"):
                current = read(account)
            entry.delete(0, "end")
            entry.insert(0, current)
            entry.configure(show=character)

    show_toggle = tk.Checkbutton(
        frame,
        text="Show keys",
        variable=show,
        command=toggle_show,
        bg="#171B22",
        fg="#B9BEC8",
        activebackground="#171B22",
        activeforeground="#E3E5EA",
        selectcolor="#20242C",
        highlightthickness=0,
        anchor="w",
    )
    show_toggle.pack(fill="x")
    tk.Label(
        frame,
        text="Leave fields blank to keep existing keys. The legacy endpoint may return HTTP 401.",
        bg="#171B22",
        fg="#AEB4C0",
        font=("Segoe UI", 9),
        anchor="w",
        wraplength=520,
    ).pack(fill="x", pady=(2, 10))

    btn_row = tk.Frame(frame, bg="#171B22")
    btn_row.pack(fill="x", side="bottom")

    def save() -> None:
        changes: dict[str, str] = {}
        for account, entry in ((ADMIN_ACCOUNT, admin), (LEGACY_ACCOUNT, legacy)):
            value = entry.get().strip()
            if not value or (value.startswith("*") and value == masked(read(account))):
                continue
            changes[account] = value
        if not changes:
            root.destroy()
            return
        try:
            for account, value in changes.items():
                keyring.set_password(SERVICE, account, value)
        except Exception:
            messagebox.showerror(
                "OpenAI Platform",
                "Unable to save credentials to Windows Credential Manager.",
                parent=root,
            )
            return
        saved["changed"] = True
        root.destroy()

    tk.Button(
        btn_row,
        text="Save & Refresh",
        command=save,
        bg="#DEB562",
        fg="#171B22",
        activebackground="#E8C378",
        relief="flat",
        padx=14,
        pady=6,
    ).pack(side="right")
    root.bind("<Return>", lambda _event: save())

    admin.focus_set()
    root.deiconify()
    root.mainloop()
    return saved["changed"]
