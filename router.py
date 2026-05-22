"""
router.py — foreground window detection -> dictation mode.

Uses pywin32 + psutil. First match in RULES wins.
Modes: polished | brand_voice | prompt | note | raw
"""

from __future__ import annotations

import re
from typing import Optional

try:
    import win32gui
    import win32process
    import psutil
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

JETBRAINS_RE = re.compile(
    r"^(idea64|pycharm64|webstorm64|goland64|clion64|rider64|datagrip64|fleet|phpstorm64)\.exe$",
    re.IGNORECASE,
)
TERMINAL_PROCESSES = {
    "windowsterminal.exe",
    "pwsh.exe",
    "powershell.exe",
    "cmd.exe",
    "wezterm.exe",
    "alacritty.exe",
}
CODE_PROCESSES = {"code.exe"}


def _get_foreground_info() -> tuple[str, str]:
    """Return (process_name_lower, window_title_lower). Falls back to empty strings."""
    if not _WIN32_AVAILABLE:
        return "", ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        title: str = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name: str = psutil.Process(pid).name()
        return process_name.lower(), title.lower()
    except Exception:
        return "", ""


def pick_mode(
    process_name: Optional[str] = None,
    window_title: Optional[str] = None,
) -> str:
    """
    Return the dictation mode for the given foreground context.

    If process_name/window_title are None, auto-detect from the OS.
    Rules (first match wins):
      1. Code / JetBrains IDEs -> raw
      2. Terminal with claude / ai / llm in title -> prompt
      3. Telegram -> note
      4. Obsidian -> brand_voice
      5. LinkedIn in any browser title -> brand_voice
      6. Otherwise -> polished

    Customise these rules to match your own apps.
    """
    if process_name is None or window_title is None:
        process_name, window_title = _get_foreground_info()

    pname = process_name.lower()
    title = window_title.lower()

    if pname in CODE_PROCESSES or JETBRAINS_RE.match(pname):
        return "raw"

    if pname in TERMINAL_PROCESSES and (
        "claude" in title or "ai" in title or "llm" in title
    ):
        return "prompt"

    if pname == "telegram.exe":
        return "note"

    if pname == "obsidian.exe":
        return "brand_voice"

    if "linkedin" in title:
        return "brand_voice"

    return "polished"


if __name__ == "__main__":
    pname, title = _get_foreground_info()
    mode = pick_mode(pname, title)
    print(f"process={pname!r}  title={title!r}  -> mode={mode}")
