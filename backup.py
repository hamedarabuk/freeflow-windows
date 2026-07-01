"""
backup.py — timestamped backup helper for user-data files.

Copies a source file to backups/<name>.<YYYYMMDD-HHMMSS>.json and prunes
the oldest backups so at most MAX_BACKUPS copies are kept per base name.

Usage:
    from backup import backup_if_changed

    backup_if_changed(Path("dictionary.json"))   # on startup or after save
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from paths import user_data_dir

log = logging.getLogger(__name__)

_BACKUP_DIR = user_data_dir() / "backups"
MAX_BACKUPS = 10


def _backup_dir() -> Path:
    _BACKUP_DIR.mkdir(exist_ok=True)
    return _BACKUP_DIR


def _existing_backups(stem: str) -> list[Path]:
    """Return all backup files for *stem*, sorted oldest-first."""
    pattern = f"{stem}.*.json"
    return sorted(_backup_dir().glob(pattern))


def _newest_backup(stem: str) -> Path | None:
    backups = _existing_backups(stem)
    return backups[-1] if backups else None


def backup_if_changed(source: Path) -> None:
    """Copy *source* to the backup directory if its content differs from
    the newest existing backup (or if no backup exists yet). Prunes the
    oldest backup when the total exceeds MAX_BACKUPS.

    Does nothing if *source* does not exist. Logs but never raises on error.
    """
    if not source.exists():
        return
    stem = source.stem  # e.g. "dictionary" from "dictionary.json"
    try:
        current_text = source.read_bytes()
    except Exception as exc:
        log.warning("backup_if_changed: cannot read %s: %s", source.name, exc)
        return

    newest = _newest_backup(stem)
    if newest is not None:
        try:
            if newest.read_bytes() == current_text:
                return  # identical to last backup — nothing to do
        except Exception:
            pass  # unreadable newest backup; proceed to create a fresh one

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = _backup_dir() / f"{stem}.{timestamp}.json"
    try:
        shutil.copy2(source, dest)
        log.info("Backed up %s -> %s", source.name, dest.name)
    except Exception as exc:
        log.warning("backup_if_changed: write failed for %s: %s", dest.name, exc)
        return

    _prune(stem)


def _prune(stem: str) -> None:
    """Remove oldest backups for *stem* so at most MAX_BACKUPS remain."""
    backups = _existing_backups(stem)
    excess = len(backups) - MAX_BACKUPS
    for old in backups[:excess]:
        try:
            old.unlink()
            log.debug("Pruned old backup %s", old.name)
        except Exception as exc:
            log.warning("backup prune failed for %s: %s", old.name, exc)
