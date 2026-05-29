"""
router.py — foreground window detection -> dictation mode.

Uses pywin32 + psutil. First match in RULES wins.
Modes: polished | brand_voice | prompt | note | raw

Routing rules are loaded from settings.py (which reads settings.json if
present). The default rule set reproduces the original hardcoded behaviour.
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

from settings import settings


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
    """Return the dictation mode for the given foreground context.

    If process_name/window_title are None, auto-detect from the OS.
    First match in settings.router_rules wins; no match returns 'polished'.
    """
    if process_name is None or window_title is None:
        process_name, window_title = _get_foreground_info()

    pname = process_name.lower()
    title = window_title.lower()

    for rule in settings.router_rules:
        match_type = rule.get("match", "process")
        pattern = rule.get("pattern", "")
        mode = rule.get("mode", "polished")

        if match_type == "process":
            if pname == pattern.lower():
                return mode

        elif match_type == "process_regex":
            if re.match(pattern, pname, re.IGNORECASE):
                return mode

        elif match_type == "title":
            process_also = rule.get("process_also")
            if process_also is not None:
                if pname not in {p.lower() for p in process_also}:
                    continue
            if pattern.lower() in title:
                return mode

    return "polished"


if __name__ == "__main__":
    pname, title = _get_foreground_info()
    mode = pick_mode(pname, title)
    print(f"process={pname!r}  title={title!r}  -> mode={mode}")
