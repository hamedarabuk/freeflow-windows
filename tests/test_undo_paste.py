"""test_undo_paste.py: unit tests for the undo-last-paste feature in main.py.

Covers _undo_last_paste()'s three branches: nothing pasted yet, the 120s undo
window expired, and the happy path (correct backspace count, state reset).
main.keyboard is replaced wholesale so no real keystroke is ever sent and no
admin-rights hook is touched. Project-root sys.path setup comes from
tests/conftest.py, the same isolation test_dispatch.py relies on.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main


@pytest.fixture(autouse=True)
def reset_paste_state():
    """Undo-paste globals must not leak between tests."""
    main._last_paste_len = 0
    main._last_paste_ts = 0.0
    yield
    main._last_paste_len = 0
    main._last_paste_ts = 0.0


@pytest.fixture
def fake_keyboard(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "keyboard", SimpleNamespace(send=lambda key: sent.append(key)))
    return sent


@pytest.fixture
def fake_tray(monkeypatch):
    notifications = []
    monkeypatch.setattr(
        main,
        "_tray",
        SimpleNamespace(notify=lambda msg, important=False: notifications.append(msg)),
    )
    return notifications


def test_nothing_to_undo(fake_keyboard, fake_tray):
    main._undo_last_paste()

    assert fake_keyboard == []
    assert fake_tray == ["Nothing to undo"]
    assert main._last_paste_len == 0


def test_undo_window_expired(monkeypatch, fake_keyboard, fake_tray):
    monkeypatch.setattr(main.time, "monotonic", lambda: 1000.0)
    main._last_paste_len = 20
    main._last_paste_ts = 1000.0 - 200  # 200s ago, past the 120s window

    main._undo_last_paste()

    assert fake_keyboard == []
    assert fake_tray == ["Undo window expired"]
    assert main._last_paste_len == 20  # untouched


def test_happy_path_sends_n_backspaces_and_resets(monkeypatch, fake_keyboard, fake_tray):
    monkeypatch.setattr(main.time, "monotonic", lambda: 1000.0)
    main._last_paste_len = 42
    main._last_paste_ts = 1000.0 - 10  # 10s ago, inside the window

    main._undo_last_paste()

    assert fake_keyboard == ["backspace"] * 42
    assert fake_tray == ["Paste undone"]
    assert main._last_paste_len == 0


def test_happy_path_caps_at_2000_backspaces(monkeypatch, fake_keyboard, fake_tray):
    monkeypatch.setattr(main.time, "monotonic", lambda: 1000.0)
    main._last_paste_len = 5000
    main._last_paste_ts = 1000.0 - 10

    main._undo_last_paste()

    assert len(fake_keyboard) == 2000
    assert fake_tray == ["Paste undone"]
    assert main._last_paste_len == 0
