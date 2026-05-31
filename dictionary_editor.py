"""
dictionary_editor.py — clickable two-column editor for the "say X, write Y"
substitutions in dictionary.json.

Replaces hand-editing the raw JSON. The user types the spoken trigger in the
left column and the written output in the right (e.g. "cloud" -> "Claude Code").
Pure UI: reads and writes go through dictionary.save_substitutions /
load_substitutions, which own all dictionary IO and the live cache.

Must be opened on the main Tk loop. The tray callback marshals via
root.after(0, ...); never call this directly from the tray thread.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import customtkinter as ctk

import dictionary

log = logging.getLogger(__name__)

# Mirror the overlay palette so the editor matches the gadget.
BG_ROOT      = "#1e1e1e"
BG_PILL      = "#2a2a2a"
FG_PRIMARY   = "#f1f5f9"
FG_SECONDARY = "#9ca3af"
FG_WARN      = "#fca5a5"
ACCENT       = "#1f3a5f"

# Module-level handle so a second tray click raises the existing window instead
# of stacking duplicates.
_window: Optional["DictionaryEditor"] = None


class DictionaryEditor(ctk.CTkToplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Word replacements")
        self.configure(fg_color=BG_ROOT)
        self.geometry("560x520")
        self.minsize(460, 360)

        # Rows of (left CTkEntry, right CTkEntry, row container frame).
        self._rows: List[Tuple[ctk.CTkEntry, ctk.CTkEntry, ctk.CTkFrame]] = []

        intro = ctk.CTkLabel(
            self,
            text=(
                "When you say the word on the left, it is written as the text "
                "on the right.\nMatching is case-insensitive and whole-word."
            ),
            text_color=FG_SECONDARY,
            justify="left",
            anchor="w",
        )
        intro.pack(fill="x", padx=16, pady=(14, 8))

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16)
        ctk.CTkLabel(
            header, text="When I say", text_color=FG_PRIMARY, anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, text="Write instead", text_color=FG_PRIMARY, anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))
        header.grid_columnconfigure(0, weight=1, uniform="cols")
        header.grid_columnconfigure(1, weight=1, uniform="cols")

        self._scroll = ctk.CTkScrollableFrame(self, fg_color=BG_PILL)
        self._scroll.pack(fill="both", expand=True, padx=16, pady=(6, 8))
        self._scroll.grid_columnconfigure(0, weight=1, uniform="cols")
        self._scroll.grid_columnconfigure(1, weight=1, uniform="cols")
        self._scroll.grid_columnconfigure(2, weight=0)

        self._status = ctk.CTkLabel(self, text="", text_color=FG_WARN, anchor="w")
        self._status.pack(fill="x", padx=16)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(4, 14))
        ctk.CTkButton(
            buttons, text="Add row", width=90, command=self._add_blank_row,
        ).pack(side="left")
        ctk.CTkButton(
            buttons, text="Close", width=90, fg_color=BG_PILL,
            command=self._close,
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="Save", width=90, command=self._save,
        ).pack(side="right", padx=(0, 8))

        self._populate()

        self.protocol("WM_DELETE_WINDOW", self._close)
        # Bring it to the foreground on open.
        self.after(80, self._raise)

    # -- rows ----------------------------------------------------------------

    def _populate(self) -> None:
        current = dictionary.load_substitutions()
        if not current:
            self._add_blank_row()
            return
        for trigger, output in current.items():
            self._add_row(trigger, output)

    def _add_row(self, trigger: str = "", output: str = "") -> None:
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        idx = len(self._rows)
        row.grid(row=idx, column=0, columnspan=3, sticky="ew", pady=3)
        row.grid_columnconfigure(0, weight=1, uniform="cols")
        row.grid_columnconfigure(1, weight=1, uniform="cols")
        row.grid_columnconfigure(2, weight=0)

        left = ctk.CTkEntry(row, placeholder_text="cloud")
        left.grid(row=0, column=0, sticky="ew")
        if trigger:
            left.insert(0, trigger)

        right = ctk.CTkEntry(row, placeholder_text="Claude Code")
        right.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        if output:
            right.insert(0, output)

        remove = ctk.CTkButton(
            row, text="Remove", width=72, fg_color=BG_PILL,
            command=lambda r=row: self._remove_row(r),
        )
        remove.grid(row=0, column=2, sticky="e", padx=(12, 0))

        self._rows.append((left, right, row))

    def _add_blank_row(self) -> None:
        self._add_row()
        self._clear_status()

    def _remove_row(self, row: ctk.CTkFrame) -> None:
        for i, (_left, _right, container) in enumerate(self._rows):
            if container is row:
                self._rows.pop(i)
                break
        row.destroy()
        self._clear_status()

    # -- save / close --------------------------------------------------------

    def _save(self) -> None:
        mapping: dict = {}
        for left, right, _container in self._rows:
            trigger = left.get().strip()
            output = right.get().strip()
            if not trigger and not output:
                continue  # fully blank row, skip
            if not trigger and output:
                self._set_status(
                    "A row has output but no spoken word. Fill the left "
                    "column or clear the row before saving."
                )
                left.focus_set()
                return
            mapping[trigger] = output  # duplicate left keys: last wins
        try:
            dictionary.save_substitutions(mapping)
        except Exception as exc:  # surface, do not crash the app
            log.warning("Failed to save substitutions: %s", exc)
            self._set_status(f"Could not save: {exc}")
            return
        self._status.configure(text="Saved.", text_color=FG_SECONDARY)

    def _set_status(self, message: str) -> None:
        self._status.configure(text=message, text_color=FG_WARN)

    def _clear_status(self) -> None:
        self._status.configure(text="")

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


def open_dictionary_editor(master) -> None:
    """Open (or raise) the word-replacements editor as a child of the running
    CTk root. MUST be called on the main Tk loop."""
    global _window
    if _window is not None and _window.winfo_exists():
        _window._raise()
        return
    _window = DictionaryEditor(master)
