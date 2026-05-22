"""
paste.py — copy text to clipboard then simulate Ctrl+V into the active window.
"""

from __future__ import annotations

import time

import keyboard
import pyperclip


def paste_text(text: str) -> None:
    pyperclip.copy(text)
    time.sleep(0.05)  # let the clipboard write settle
    keyboard.send("ctrl+v")
