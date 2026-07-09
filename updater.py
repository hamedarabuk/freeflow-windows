"""
updater.py — non-blocking startup version check for FreeFlow.

Fetches a version.json from a remote URL and compares to the local version.
If a newer version is available it logs a notice and attempts a tray balloon.
Never blocks startup; never crashes the app if offline.

Wire into main() like this:

    from updater import check_for_update_async
    check_for_update_async(tray=_tray)   # call after _tray.start()

The check runs on a daemon thread and returns immediately.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import requests

from version import __version__

log = logging.getLogger(__name__)

# version.json is served from the public repo's main branch. The file's own
# "url" field points at the FreeFlow-Setup.exe asset on the matching GitHub
# Release, so a new release is picked up by bumping version.json on main.
VERSION_CHECK_URL = "https://raw.githubusercontent.com/hamedarabuk/freeflow-windows/main/version.json"

_CHECK_TIMEOUT_S = 5


def _parse_version(v: str) -> tuple[int, ...]:
    """Convert a semver string like '2.1.0' to a comparable tuple."""
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)


def _do_check(tray) -> None:
    """Fetch version.json and emit a notice if a newer version exists."""
    try:
        resp = requests.get(VERSION_CHECK_URL, timeout=_CHECK_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        remote_version = str(data.get("version", "")).strip()
        download_url = str(data.get("url", "")).strip()
        notes = str(data.get("notes", "")).strip()
    except Exception as exc:
        # Offline, DNS failure, timeout, or bad JSON: silently ignore.
        log.debug("Version check skipped: %s", exc)
        return

    if not remote_version:
        log.debug("Version check: empty version in response.")
        return

    local_tuple = _parse_version(__version__)
    remote_tuple = _parse_version(remote_version)

    if remote_tuple <= local_tuple:
        log.debug("Version check: up to date (%s).", __version__)
        return

    message = (
        f"FreeFlow {remote_version} is available (you have {__version__}). "
        f"Download: {download_url}"
    )
    if notes:
        message += f" | {notes}"

    log.info("Update available: %s", message)
    print(f"[FreeFlow] {message}")

    # Surface a tray balloon if possible. Fails silently if tray is not ready.
    if tray is not None:
        try:
            tray.notify(
                f"FreeFlow {remote_version} available. "
                f"Download the new installer from {download_url}"
            )
        except Exception as exc:
            log.debug("Tray notify for update failed: %s", exc)


def check_for_update_async(tray=None) -> None:
    """Start the version check on a background daemon thread.

    Args:
        tray: a TrayIcon instance (optional). When provided, a tray balloon
              is shown if a newer version is available.
    """
    t = threading.Thread(target=_do_check, args=(tray,), daemon=True, name="freeflow-updater")
    t.start()
