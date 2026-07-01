"""
about.py — constant values for the About window, help/report tray entries,
and the first-run welcome dialog.

Kept tiny and dependency-light (no tkinter import here) so these values are
trivial to change later without hunting through UI code.
"""

from __future__ import annotations

from version import __version__

APP_NAME = "FreeFlow"
VERSION = __version__
DESCRIPTION = (
    "Hold-to-talk dictation for Windows. Speak, and clean, polished text "
    "is pasted instantly."
)
AUTHOR = "Hamed Arab Choobdar"
WEBSITE_URL = "https://www.hamedarab.academy/"
SUPPORT_EMAIL = "hamed@hamedarab.academy"
SOURCE_URL = "https://github.com/hamedarabuk/freeflow-windows"
