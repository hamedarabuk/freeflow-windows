"""
tray.py — pystray system-tray icon with three states.

States: idle (grey), recording (red), processing (amber).
The tray runs in its own thread; state changes are thread-safe.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw
import pystray

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
    ) -> None:
        self._on_pause_toggle = on_pause_toggle
        self._on_force_mode = on_force_mode
        self._on_show_last = on_show_last
        self._on_open_logs = on_open_logs
        self._on_quit = on_quit
        self._on_show_gadget = on_show_gadget
        self._on_edit_dictionary = on_edit_dictionary
        self._on_edit_snippets = on_edit_snippets
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
        items.extend(mode_items)
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
        items.extend([
            pystray.MenuItem("Show last 10", lambda _: self._on_show_last()),
            pystray.MenuItem("Open log folder", lambda _: self._on_open_logs()),
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

    def notify(self, message: str) -> None:
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
