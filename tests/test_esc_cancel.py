"""test_esc_cancel.py: unit tests for the Esc mid-recording cancel gesture.

The Esc hook is armed only while a hold-to-talk recording is in flight
(registered after recorder.start(), removed on normal release or on the
cancel itself), so Esc behaves normally at all other times. Cancelling
stops the recorder, deletes the clip, dispatches nothing, and returns the
UI to idle.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main


class _FakeWav:
    def __init__(self) -> None:
        self.unlink_calls = 0

    def unlink(self, missing_ok: bool = False) -> None:
        self.unlink_calls += 1


class _FakeRecorder:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.wav = _FakeWav()

    def start(self) -> None:
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1
        return self.wav


@pytest.fixture
def cancel_env(monkeypatch):
    """Reset hotkey/cancel state and neutralise hardware seams. Fakes the
    keyboard hook registry so arming/disarming is observable without real
    OS hooks."""
    monkeypatch.setattr(main, "_hotkey_claimed", False)
    monkeypatch.setattr(main, "_recording_active", False)
    monkeypatch.setattr(main, "_press_start_time", 0.0)
    monkeypatch.setattr(main, "_last_tap_release_time", 0.0)
    monkeypatch.setattr(main, "_session_active", False)
    monkeypatch.setattr(main, "_paused", False)
    monkeypatch.setattr(main, "_tray", None)
    monkeypatch.setattr(main, "_overlay", None)
    monkeypatch.setattr(main, "_esc_hook", None)

    fake_recorder = _FakeRecorder()
    monkeypatch.setattr(main, "_recorder", fake_recorder)

    modifier_state = SimpleNamespace(held=True)
    monkeypatch.setattr(main.keyboard, "is_pressed", lambda key: modifier_state.held)

    hooks = SimpleNamespace(registered=[], unhooked=[])

    def _fake_on_press_key(key, callback, suppress=False):
        handle = object()
        hooks.registered.append((key, callback, suppress, handle))
        return handle

    def _fake_unhook(handle):
        hooks.unhooked.append(handle)

    monkeypatch.setattr(main.keyboard, "on_press_key", _fake_on_press_key)
    monkeypatch.setattr(main.keyboard, "unhook", _fake_unhook)

    clock = SimpleNamespace(t=1000.0)

    def _monotonic():
        clock.t += 0.01
        return clock.t

    monkeypatch.setattr(main.time, "monotonic", _monotonic)

    event = SimpleNamespace(name=main.settings.hotkey, scan_code=0)
    esc_event = SimpleNamespace(name="esc", scan_code=1)
    return SimpleNamespace(
        recorder=fake_recorder,
        modifier=modifier_state,
        event=event,
        esc_event=esc_event,
        hooks=hooks,
        clock=clock,
    )


def test_esc_hook_armed_on_record_start(cancel_env):
    """Starting a hold-to-talk recording registers exactly one suppressed
    Esc hook."""
    assert main._on_alt1_press(cancel_env.event) is False
    esc_hooks = [h for h in cancel_env.hooks.registered if h[0] == "esc"]
    assert len(esc_hooks) == 1
    assert esc_hooks[0][2] is True  # suppress=True
    assert main._esc_hook is not None


def test_esc_cancels_recording_and_discards_clip(cancel_env):
    """Esc during a recording stops the recorder, deletes the clip, swallows
    the keystroke, and disarms its own hook."""
    main._on_alt1_press(cancel_env.event)

    assert main._on_esc_cancel(cancel_env.esc_event) is False
    assert main._recording_active is False
    assert cancel_env.recorder.stop_calls == 1
    assert cancel_env.recorder.wav.unlink_calls == 1
    assert main._esc_hook is None
    assert len(cancel_env.hooks.unhooked) == 1


def test_release_after_cancel_dispatches_nothing(cancel_env):
    """The hotkey release following a cancel must not stop the recorder a
    second time or dispatch anything; the claim still clears."""
    main._on_alt1_press(cancel_env.event)
    main._on_esc_cancel(cancel_env.esc_event)

    # Hold long enough that the release cannot read as a double-tap.
    cancel_env.clock.t += 1.0
    assert main._on_alt1_release(cancel_env.event) is False
    assert cancel_env.recorder.stop_calls == 1
    assert main._hotkey_claimed is False


def test_esc_ignored_when_not_recording(cancel_env):
    """A stray Esc with no recording in flight passes through and stops
    nothing."""
    assert main._on_esc_cancel(cancel_env.esc_event) is True
    assert cancel_env.recorder.stop_calls == 0


def test_normal_release_disarms_esc_hook(cancel_env):
    """A normal (uncancelled) stop also removes the Esc hook, so Esc is
    never intercepted between dictations."""
    main._on_alt1_press(cancel_env.event)
    cancel_env.clock.t += 1.0
    main._on_alt1_release(cancel_env.event)
    assert main._esc_hook is None
    assert len(cancel_env.hooks.unhooked) == 1
