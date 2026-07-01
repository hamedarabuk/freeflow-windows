"""
about_window.py — small "About FreeFlow" window with clickable links.

Must be opened on the main Tk loop. The tray callback marshals via
root.after(0, ...); never call this directly from the tray thread.
"""

from __future__ import annotations

import logging
import webbrowser
from typing import Optional

import customtkinter as ctk

from about import APP_NAME, AUTHOR, DESCRIPTION, SOURCE_URL, SUPPORT_EMAIL, VERSION, WEBSITE_URL

log = logging.getLogger(__name__)

# Mirror the overlay palette so the window matches the gadget.
BG_ROOT      = "#1e1e1e"
FG_PRIMARY   = "#f1f5f9"
FG_SECONDARY = "#9ca3af"
LINK_COLOUR  = "#60a5fa"

# Module-level handle so a second tray click raises the existing window
# instead of stacking duplicates.
_window: Optional["AboutWindow"] = None


class AboutWindow(ctk.CTkToplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title(f"About {APP_NAME}")
        self.configure(fg_color=BG_ROOT)
        self.geometry("420x320")
        self.resizable(False, False)

        ctk.CTkLabel(
            self,
            text=f"{APP_NAME} v{VERSION}",
            text_color=FG_PRIMARY,
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(padx=20, pady=(20, 6), anchor="w")

        ctk.CTkLabel(
            self,
            text=DESCRIPTION,
            text_color=FG_SECONDARY,
            wraplength=380,
            justify="left",
            anchor="w",
        ).pack(padx=20, pady=(0, 10), anchor="w")

        ctk.CTkLabel(
            self,
            text=f"Built by {AUTHOR}",
            text_color=FG_SECONDARY,
            anchor="w",
        ).pack(padx=20, pady=(0, 10), anchor="w")

        self._add_link("Website", WEBSITE_URL, lambda: webbrowser.open(WEBSITE_URL))
        self._add_link(
            "Support",
            SUPPORT_EMAIL,
            lambda: webbrowser.open(f"mailto:{SUPPORT_EMAIL}"),
        )
        self._add_link("Source", SOURCE_URL, lambda: webbrowser.open(SOURCE_URL))

        ctk.CTkLabel(
            self,
            text="For updates and help, visit hamedarab.academy",
            text_color=FG_SECONDARY,
            wraplength=380,
            justify="left",
            anchor="w",
        ).pack(padx=20, pady=(10, 10), anchor="w")

        ctk.CTkButton(
            self, text="Close", width=90, command=self._close,
        ).pack(pady=(0, 16))

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(80, self._raise)

    def _add_link(self, label: str, text: str, on_click) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=20, pady=2, anchor="w")
        ctk.CTkLabel(
            row, text=f"{label}: ", text_color=FG_SECONDARY,
        ).pack(side="left")
        link = ctk.CTkLabel(
            row, text=text, text_color=LINK_COLOUR, cursor="hand2",
        )
        link.pack(side="left")
        link.bind("<Button-1>", lambda _event: on_click())

    def _raise(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    def _close(self) -> None:
        global _window
        _window = None
        self.destroy()


def open_about_window(master) -> None:
    """Open (or raise) the About window as a child of the running CTk root.
    MUST be called on the main Tk loop."""
    global _window
    if _window is not None and _window.winfo_exists():
        _window._raise()
        return
    _window = AboutWindow(master)
