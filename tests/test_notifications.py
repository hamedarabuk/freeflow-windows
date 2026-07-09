"""Tests for the notify_level toast gate (tray.notify + settings).

The gate reads settings.notify_level live at call time: "all" shows every
toast, "important" (the default) shows only calls flagged important=True,
"silent" shows none. Invalid values in settings.json fall back to
"important" with a warning.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

import settings as settings_module
import tray as tray_module
from settings import DictationSettings


@pytest.fixture
def gated_tray(monkeypatch):
    """A TrayIcon with a fake pystray icon capturing sent toasts, plus a
    helper to set the live notify_level without touching the real file."""
    icon_calls: list[str] = []
    t = tray_module.TrayIcon.__new__(tray_module.TrayIcon)
    t._icon = SimpleNamespace(notify=lambda msg, title: icon_calls.append(msg))

    def set_level(level: str) -> None:
        # DictationSettings is a frozen dataclass; object.__setattr__ is the
        # same escape hatch settings.set_notify_level uses internally.
        object.__setattr__(settings_module.settings, "notify_level", level)

    original = settings_module.settings.notify_level
    yield t, icon_calls, set_level
    object.__setattr__(settings_module.settings, "notify_level", original)


def test_default_level_is_important():
    assert DictationSettings().notify_level == "important"


def test_important_level_suppresses_routine(gated_tray):
    t, calls, set_level = gated_tray
    set_level("important")
    t.notify("Session: ON")
    t.notify("Mode locked: note")
    assert calls == []


def test_important_level_shows_important(gated_tray):
    t, calls, set_level = gated_tray
    set_level("important")
    t.notify("Transcription failed.", important=True)
    assert calls == ["Transcription failed."]


def test_all_level_shows_everything(gated_tray):
    t, calls, set_level = gated_tray
    set_level("all")
    t.notify("Session: ON")
    t.notify("Transcription failed.", important=True)
    assert calls == ["Session: ON", "Transcription failed."]


def test_silent_level_shows_nothing(gated_tray):
    t, calls, set_level = gated_tray
    set_level("silent")
    t.notify("Session: ON")
    t.notify("Transcription failed.", important=True)
    assert calls == []


def test_invalid_level_in_settings_json_falls_back(
    isolated_settings_file, caplog
):
    isolated_settings_file.write_text(
        json.dumps({"notify_level": "loud"}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="settings"):
        loaded = settings_module._load_settings()
    assert loaded.notify_level == "important"
    assert any("notify_level" in rec.message for rec in caplog.records)
