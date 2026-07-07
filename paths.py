"""
paths.py — stable per-user data locations for FreeFlow.

The packaged PyInstaller build resolves Path(__file__) inside the install
folder (AppData\\Local\\FreeFlow\\_internal\\), which is wiped on every
reinstall/update. User-editable data (dictionary.json, snippets.json,
settings.json, backups/) must live somewhere stable instead, alongside the
existing %APPDATA%\\FreeFlow\\config.json used for the Groq key.

This module must NOT import any other project module, to avoid import cycles.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "FreeFlow"


def user_data_dir() -> Path:
    """Stable per-user data directory: %APPDATA%\\FreeFlow (created if missing)."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        path = Path(appdata) / APP_NAME
    else:
        path = Path.home() / "AppData" / "Roaming" / APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        path = Path.home() / ".freeflow"
        path.mkdir(parents=True, exist_ok=True)
    return path


def user_file(name: str) -> Path:
    """Path to a user-editable data file inside the stable per-user directory."""
    return user_data_dir() / name


def logs_dir() -> Path:
    """Stable per-user logs directory: %APPDATA%\\FreeFlow\\logs (created if missing).

    Mirrors user_data_dir(): the frozen build's install folder can be wiped on
    every reinstall/update and is not a reliable place to write logs, so every
    log writer (dictation history, quality-guard records, drag diagnostics,
    the general application log) must live under this stable per-user
    location instead. Works identically from source and frozen runs.
    """
    path = user_data_dir() / "logs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.warning("Failed to create logs directory %s", path)
    return path


def resource_dir() -> Path:
    """Directory of bundled READ-ONLY resources (frozen or source-run)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def resource_file(name: str) -> Path:
    """Path to a bundled read-only resource file."""
    return resource_dir() / name


def migrate_legacy_user_data() -> None:
    """One-time migration of user data from the old code-directory location.

    For dictionary.json, snippets.json, and settings.json: if the stable
    per-user copy does not already exist AND a legacy copy exists next to
    this module (the old code-directory location, i.e. the source repo in
    dev runs), copy the legacy file across. Never overwrites an existing
    user file.
    """
    legacy_dir = Path(__file__).resolve().parent
    for name in ("dictionary.json", "snippets.json", "settings.json"):
        target = user_file(name)
        if target.exists():
            continue
        legacy = legacy_dir / name
        if not legacy.exists():
            continue
        try:
            shutil.copy2(legacy, target)
            log.info("Migrated legacy %s to %s", legacy, target)
        except Exception as exc:
            log.warning("Failed to migrate legacy %s: %s", legacy, exc)
