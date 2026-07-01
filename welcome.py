"""
welcome.py — first-run welcome dialog, shown once before the CTk overlay
starts.

Mirrors config.py's _run_wizard: a plain tkinter dialog with its own
mainloop, run and destroyed before the CTk overlay is created. Never call
this after the overlay root exists; a second Tk root alongside a live
CTk root crashes the app.
"""

from __future__ import annotations

import logging
import tkinter as tk
import webbrowser

from about import SUPPORT_EMAIL, WEBSITE_URL
from paths import user_file

log = logging.getLogger(__name__)

_MARKER_NAME = ".welcomed"


def _show_dialog() -> None:
    root = tk.Tk()
    root.title("Welcome to FreeFlow")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    root.update_idletasks()
    w, h = 460, 320
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    tk.Label(
        root,
        text="Welcome to FreeFlow",
        font=("Segoe UI", 13, "bold"),
        pady=6,
    ).pack(padx=20, anchor="w")

    lines = [
        "Hold Alt+1 anywhere and speak. Release to paste clean text.",
        "The floating gadget (bottom-right) shows the status and current "
        "mode. Click it to change mode or turn on Translate to English.",
        "Double-tap Alt+1 for hands-free session mode.",
        "Right-click the tray icon to edit your dictionary and snippets, "
        "or to get help.",
    ]
    for line in lines:
        tk.Label(
            root,
            text=line,
            wraplength=420,
            justify="left",
            font=("Segoe UI", 10),
        ).pack(padx=20, pady=(4, 0), anchor="w")

    tk.Label(
        root,
        text=f"Help and updates: hamedarab.academy   |   Support: {SUPPORT_EMAIL}",
        wraplength=420,
        justify="left",
        font=("Segoe UI", 9),
        fg="#555555",
    ).pack(padx=20, pady=(10, 10), anchor="w")

    def _visit_website() -> None:
        webbrowser.open(WEBSITE_URL)

    def _close() -> None:
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=4)
    tk.Button(
        btn_frame, text="Visit website", command=_visit_website, width=14,
        font=("Segoe UI", 10),
    ).pack(side="left", padx=6)
    tk.Button(
        btn_frame, text="Got it", command=_close, width=10,
        font=("Segoe UI", 10),
    ).pack(side="left", padx=6)

    root.bind("<Return>", lambda _event: _close())
    root.bind("<Escape>", lambda _event: _close())
    root.protocol("WM_DELETE_WINDOW", _close)

    root.mainloop()


def show_welcome_once() -> None:
    """Show the first-run welcome dialog, then persist a marker so it never
    shows again. Never raises: any failure is logged and swallowed so a
    broken dialog cannot block startup."""
    marker = user_file(_MARKER_NAME)
    if marker.exists():
        return
    try:
        _show_dialog()
    except Exception as exc:
        log.warning("Welcome dialog failed: %s", exc)
    try:
        marker.write_text("", encoding="utf-8")
    except Exception as exc:
        log.warning("Failed to write welcome marker %s: %s", marker, exc)
