"""conftest.py — shared fixtures and path setup for the FreeFlow test suite.

settings.py, quality_guard.py, router.py, transcribe.py and paths.py are
plain modules at the repo root (not a package), so the root must be on
sys.path for a direct `import settings` etc. to work regardless of the
directory pytest is invoked from.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest


@pytest.fixture(autouse=True, scope="session")
def _strip_real_file_logging():
    """Detach any file handlers from the root logger for the whole test run.

    main.py attaches a RotatingFileHandler pointing at the real
    %APPDATA%\\FreeFlow\\logs\\app.log at import time, before any test
    fixture can redirect paths. Without this guard, test-generated warnings
    (deliberate failure scenarios) leak into the live app.log and pollute
    real diagnostics. caplog captures records at the logger level, so
    removing file handlers does not affect assertions.
    """
    import logging
    import logging.handlers

    root = logging.getLogger()
    removed = [
        h for h in root.handlers
        if isinstance(h, logging.FileHandler)
    ]
    for h in removed:
        root.removeHandler(h)
        h.close()

    # main.py may be imported later in the session; strip again lazily via a
    # filter that blocks nothing but lets us re-check on first record. Simpler
    # and robust: patch RotatingFileHandler.emit to a no-op for the session.
    real_emit = logging.handlers.RotatingFileHandler.emit
    logging.handlers.RotatingFileHandler.emit = lambda self, record: None
    yield
    logging.handlers.RotatingFileHandler.emit = real_emit


@pytest.fixture
def isolated_settings_file(tmp_path, monkeypatch):
    """Redirect settings.py's file-backed state at a tmp_path file.

    Patches both the module-level `_SETTINGS_FILE` constant (read by
    `_load_settings`) and the `user_file` name imported into settings.py
    (read fresh on every call by `save_setting`), so no test ever touches
    the real %APPDATA%\\FreeFlow\\settings.json.
    """
    import settings as settings_module

    fake_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "_SETTINGS_FILE", fake_file)
    monkeypatch.setattr(settings_module, "user_file", lambda name: tmp_path / name)
    return fake_file
