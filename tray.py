"""
tray.py — pystray system-tray icon with three states.

States: idle (grey), recording (red), processing (amber).
The tray runs in its own thread; state changes are thread-safe.
"""

from __future__ import annotations

import os
import threading
import webbrowser
from typing import Callable, Optional

from PIL import Image, ImageDraw
import pystray

from about import SUPPORT_EMAIL, WEBSITE_URL
import settings as settings_module

ICON_SIZE = 16

COLOUR_IDLE = "#808080"
COLOUR_RECORDING = "#e53935"
COLOUR_PROCESSING = "#fb8c00"


def _make_icon(colour: str) -> Image.Image:
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, ICON_SIZE - 2, ICON_SIZE - 2], fill=colour)
    return img


class TrayIcon:
    MODES = ["polished", "brand_voice", "prompt", "note", "raw"]
    # Language submenu: (display label, dictation_language value).
    LANGUAGES = [("English", "en"), ("Farsi", "fa"), ("Auto", "auto")]
    # Notifications submenu: (display label, notify_level value).
    NOTIFY_LEVELS = [("All", "all"), ("Important only", "important"), ("Off", "silent")]

    def __init__(
        self,
        on_pause_toggle: Callable[[], None],
        on_force_mode: Callable[[str], None],
        on_show_last: Callable[[], None],
        on_open_logs: Callable[[], None],
        on_quit: Callable[[], None],
        on_show_gadget: Optional[Callable[[], None]] = None,
        on_edit_dictionary: Optional[Callable[[], None]] = None,
        on_edit_snippets: Optional[Callable[[], None]] = None,
        on_about: Optional[Callable[[], None]] = None,
        on_set_language: Optional[Callable[[str], None]] = None,
        get_dictation_language: Optional[Callable[[], str]] = None,
        on_undo_paste: Optional[Callable[[], None]] = None,
        on_meeting_toggle: Optional[Callable[[], None]] = None,
        get_meeting_active: Optional[Callable[[], bool]] = None,
        on_compact_toggle: Optional[Callable[[], None]] = None,
        get_compact: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._on_pause_toggle = on_pause_toggle
        self._on_force_mode = on_force_mode
        self._on_show_last = on_show_last
        self._on_open_logs = on_open_logs
        self._on_quit = on_quit
        self._on_show_gadget = on_show_gadget
        self._on_edit_dictionary = on_edit_dictionary
        self._on_edit_snippets = on_edit_snippets
        self._on_about = on_about
        self._on_undo_paste = on_undo_paste
        self._on_set_language = on_set_language
        self._get_dictation_language = get_dictation_language or (lambda: "en")
        self._on_meeting_toggle = on_meeting_toggle
        self._get_meeting_active = get_meeting_active or (lambda: False)
        self._on_compact_toggle = on_compact_toggle
        self._get_compact = get_compact or (lambda: False)
        self._paused = False
        self._icon: Optional[pystray.Icon] = None
        self._lock = threading.Lock()

    def _build_menu(self) -> pystray.Menu:
        pause_label = "Resume" if self._paused else "Pause"
        mode_items = [
            pystray.MenuItem(
                f"Lock to {m}",
                lambda _, m=m: self._on_force_mode(m),
            )
            for m in self.MODES
        ]
        items = [
            pystray.MenuItem(pause_label, lambda _: self._on_pause_toggle()),
            pystray.Menu.SEPARATOR,
        ]
        if self._on_show_gadget is not None:
            items.append(
                pystray.MenuItem(
                    "Show gadget",
                    lambda _: self._on_show_gadget(),
                    default=True,
                )
            )
            items.append(pystray.Menu.SEPARATOR)
        if self._on_compact_toggle is not None:
            items.append(
                pystray.MenuItem(
                    "Compact gadget",
                    lambda _: self._on_compact_toggle(),
                    checked=lambda item: self._get_compact(),
                )
            )
            items.append(pystray.Menu.SEPARATOR)
        if self._on_meeting_toggle is not None:
            meeting_label = (
                "Stop meeting notes" if self._get_meeting_active() else "Start meeting notes"
            )
            items.append(
                pystray.MenuItem(meeting_label, lambda _: self._on_meeting_toggle())
            )
            items.append(pystray.Menu.SEPARATOR)
        items.extend(mode_items)
        items.append(pystray.Menu.SEPARATOR)
        if self._on_set_language is not None:
            lang_items = [
                pystray.MenuItem(
                    label,
                    lambda _, v=value: self._on_set_language(v),
                    checked=lambda item, v=value: self._get_dictation_language() == v,
                    radio=True,
                )
                for label, value in self.LANGUAGES
            ]
            items.append(pystray.MenuItem("Language", pystray.Menu(*lang_items)))
            items.append(pystray.Menu.SEPARATOR)
        notify_items = [
            pystray.MenuItem(
                label,
                lambda _, v=value: settings_module.set_notify_level(v),
                checked=lambda item, v=value: settings_module.settings.notify_level == v,
                radio=True,
            )
            for label, value in self.NOTIFY_LEVELS
        ]
        items.append(pystray.MenuItem("Notifications", pystray.Menu(*notify_items)))
        items.append(pystray.Menu.SEPARATOR)
        if self._on_edit_dictionary is not None:
            items.append(
                pystray.MenuItem(
                    "Edit dictionary",
                    lambda _: self._on_edit_dictionary(),
                )
            )
        if self._on_edit_snippets is not None:
            items.append(
                pystray.MenuItem(
                    "Edit snippets",
                    lambda _: self._on_edit_snippets(),
                )
            )
        if self._on_undo_paste is not None:
            items.append(
                pystray.MenuItem(
                    "Undo last paste",
                    lambda _: self._on_undo_paste(),
                )
            )
        items.extend([
            pystray.MenuItem("Show last 10", lambda _: self._on_show_last()),
            pystray.MenuItem("Open log folder", lambda _: self._on_open_logs()),
            pystray.Menu.SEPARATOR,
        ])
        if self._on_about is not None:
            items.append(
                pystray.MenuItem("About FreeFlow", lambda _: self._on_about())
            )
        items.extend([
            pystray.MenuItem(
                "Help and updates", lambda _: webbrowser.open(WEBSITE_URL)
            ),
            pystray.MenuItem(
                "Report an issue",
                lambda _: webbrowser.open(
                    f"mailto:{SUPPORT_EMAIL}?subject=FreeFlow issue"
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit dictation service", lambda _: self._on_quit()),
        ])
        return pystray.Menu(*items)

    def start(self) -> None:
        self._icon = pystray.Icon(
            "dictation",
            _make_icon(COLOUR_IDLE),
            "Dictation: idle",
            menu=self._build_menu(),
        )
        threading.Thread(target=self._icon.run, daemon=True).start()

    def _update(self, colour: str, tooltip: str) -> None:
        if self._icon:
            self._icon.icon = _make_icon(colour)
            self._icon.title = tooltip

    def set_idle(self) -> None:
        self._update(COLOUR_IDLE, "Dictation: idle")

    def set_recording(self) -> None:
        self._update(COLOUR_RECORDING, "Dictation: recording...")

    def set_processing(self) -> None:
        self._update(COLOUR_PROCESSING, "Dictation: processing...")

    def notify(self, message: str, important: bool = False) -> None:
        """Show a Windows toast, gated by the user's notify_level setting.

        Reads settings.notify_level live at call time via the settings
        module (not a value cached on this instance), so a change from the
        Notifications submenu takes effect on the very next call. "all"
        shows everything, "important" (default) shows only messages the
        caller flags important=True, "silent" shows none.
        """
        level = settings_module.settings.notify_level
        if level == "silent":
            return
        if level == "important" and not important:
            return
        if self._icon:
            try:
                self._icon.notify(message, "Dictation")
            except Exception:
                pass

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._icon.menu = self._build_menu()  # type: ignore[union-attr]
        self._icon.update_menu()  # type: ignore[union-attr]

    def refresh_language(self) -> None:
        """Rebuild the menu so the Language submenu's radio checkmark reflects
        the current dictation_language (e.g. after the overlay pill cycles it)."""
        if self._icon:
            self._icon.menu = self._build_menu()  # type: ignore[union-attr]
            self._icon.update_menu()  # type: ignore[union-attr]

    def refresh_meeting_state(self) -> None:
        """Rebuild the menu so the meeting notes label reflects the current
        start/stop state."""
        if self._icon:
            self._icon.menu = self._build_menu()  # type: ignore[union-attr]
            self._icon.update_menu()  # type: ignore[union-attr]

    def refresh_compact_state(self) -> None:
        """Rebuild the menu so the Compact gadget checkmark reflects the
        current state (compact can also be toggled from the gadget's own
        mode menu, not just this tray item)."""
        if self._icon:
            self._icon.menu = self._build_menu()  # type: ignore[union-attr]
            self._icon.update_menu()  # type: ignore[union-attr]
