"""
snippets_editor.py — clickable editor for voice snippets in snippets.json.

Replaces hand-editing the raw JSON. The user types a spoken cue in the left
column and the text it should type in the right. Unlike word replacements,
snippet expansions are often paragraphs, so the right column is a multi-line
box that round-trips newlines: dictate the cue on its own and the whole
expansion is pasted verbatim (LLM cleanup is skipped).

Pure UI: reads and writes go through snippets.save_snippets / load_snippets,
which own all snippet IO and the live cache.

Must be opened on the main Tk loop. The tray callback marshals via
root.after(0, ...); never call this directly from the tray thread.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import customtkinter as ctk

import snippets

log = logging.getLogger(__name__)

# Mirror the overlay palette so the editor matches the gadget.
BG_ROOT      = "#1e1e1e"
BG_PILL      = "#2a2a2a"
FG_PRIMARY   = "#f1f5f9"
FG_SECONDARY = "#9ca3af"
FG_WARN      = "#fca5a5"
ACCENT       = "#1f3a5f"

# Recommended starter snippets, used by "Add recommended". Mix of single- and
# multi-line; generic and clearly editable. Triggers are plain lowercase words
# so they fire under the cue match (case-insensitive, trailing punctuation
# ignored). Keep this in sync with snippets.json.example.
RECOMMENDED: List[Tuple[str, str]] = [
    ("email signoff", "Kind regards,\nHamed"),
    ("thanks reply", "Thank you so much, I really appreciate it."),
    ("follow up", "Just following up on my previous message. Let me know if "
                  "there is anything you need from me."),
    ("intro line", "Hi [name],\n\nThanks for getting in touch."),
    ("meeting availability", "I am generally free on weekday mornings. What "
                             "time suits you best?"),
    ("booking link", "Here is my booking link: [paste your link]"),
    ("studio address", "[Your studio address]\nJewellery Quarter, Birmingham"),
]

# Module-level handle so a second tray click raises the existing window instead
# of stacking duplicates.
_window: Optional["SnippetsEditor"] = None


class SnippetsEditor(ctk.CTkToplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Snippets")
        self.configure(fg_color=BG_ROOT)
        self.geometry("640x560")
        self.minsize(520, 380)

        # Rows of (trigger CTkEntry, expansion CTkTextbox, row container frame).
        self._rows: List[Tuple[ctk.CTkEntry, ctk.CTkTextbox, ctk.CTkFrame]] = []

        intro = ctk.CTkLabel(
            self,
            text=(
                "Dictate a cue on its own and the text on the right is typed "
                "for you, exactly as written.\nThe cue match is "
                "case-insensitive and ignores trailing punctuation."
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
            header, text="It types this", text_color=FG_PRIMARY, anchor="w",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))
        header.grid_columnconfigure(0, weight=1, uniform="cols")
        header.grid_columnconfigure(1, weight=2, uniform="cols")
        header.grid_columnconfigure(2, weight=0)

        self._scroll = ctk.CTkScrollableFrame(self, fg_color=BG_PILL)
        self._scroll.pack(fill="both", expand=True, padx=16, pady=(6, 8))
        self._scroll.grid_columnconfigure(0, weight=1, uniform="cols")
        self._scroll.grid_columnconfigure(1, weight=2, uniform="cols")
        self._scroll.grid_columnconfigure(2, weight=0)

        self._status = ctk.CTkLabel(self, text="", text_color=FG_WARN, anchor="w")
        self._status.pack(fill="x", padx=16)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(4, 14))
        ctk.CTkButton(
            buttons, text="Add row", width=90, command=self._add_blank_row,
        ).pack(side="left")
        ctk.CTkButton(
            buttons, text="Add recommended", width=140, fg_color=BG_PILL,
            command=self._add_recommended,
        ).pack(side="left", padx=(8, 0))
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
        current = snippets.load_snippets()
        if not current:
            self._add_blank_row()
            return
        for trigger, expansion in current.items():
            self._add_row(trigger, expansion)

    def _add_row(self, trigger: str = "", expansion: str = "") -> None:
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        idx = len(self._rows)
        row.grid(row=idx, column=0, columnspan=3, sticky="ew", pady=3)
        row.grid_columnconfigure(0, weight=1, uniform="cols")
        row.grid_columnconfigure(1, weight=2, uniform="cols")
        row.grid_columnconfigure(2, weight=0)

        left = ctk.CTkEntry(row, placeholder_text="email signoff")
        left.grid(row=0, column=0, sticky="new")
        if trigger:
            left.insert(0, trigger)

        # Multi-line so paragraph snippets round-trip newlines. ~4 lines tall.
        right = ctk.CTkTextbox(row, height=88, wrap="word")
        right.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        if expansion:
            right.insert("1.0", expansion)

        remove = ctk.CTkButton(
            row, text="Remove", width=72, fg_color=BG_PILL,
            command=lambda r=row: self._remove_row(r),
        )
        remove.grid(row=0, column=2, sticky="ne", padx=(12, 0))

        self._rows.append((left, right, row))

    def _add_blank_row(self) -> None:
        self._add_row()
        self._clear_status()

    def _add_recommended(self) -> None:
        """Append recommended starters that are not already present as rows.
        Non-destructive: never clobbers existing rows, never auto-saves. The
        user reviews, removes any they dislike, then Saves."""
        existing = {
            snippets._normalise(left.get())
            for left, _right, _container in self._rows
            if left.get().strip()
        }
        added = 0
        for trigger, expansion in RECOMMENDED:
            if snippets._normalise(trigger) in existing:
                continue
            self._add_row(trigger, expansion)
            existing.add(snippets._normalise(trigger))
            added += 1
        if added:
            self._status.configure(
                text=f"Added {added} recommended snippet(s). Review, then Save.",
                text_color=FG_SECONDARY,
            )
        else:
            self._status.configure(
                text="All recommended snippets are already in the list.",
                text_color=FG_SECONDARY,
            )

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
            expansion = right.get("1.0", "end-1c")
            if not trigger and not expansion.strip():
                continue  # fully blank row, skip
            if not trigger and expansion.strip():
                self._set_status(
                    "A row has text but no spoken cue. Fill the left column "
                    "or clear the row before saving."
                )
                left.focus_set()
                return
            mapping[trigger] = expansion  # duplicate cues: last wins
        try:
            snippets.save_snippets(mapping)
        except Exception as exc:  # surface, do not crash the app
            log.warning("Failed to save snippets: %s", exc)
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


def open_snippets_editor(master) -> None:
    """Open (or raise) the snippets editor as a child of the running CTk root.
    MUST be called on the main Tk loop."""
    global _window
    if _window is not None and _window.winfo_exists():
        _window._raise()
        return
    _window = SnippetsEditor(master)
