"""test_hotkey_sticky_claim.py: unit tests for the Alt+<hotkey> hold-to-talk
sticky-claim state machine in main.py (_on_alt1_press / _on_alt1_release).

Regression coverage for the reported bug: dictating into Telegram with the
default hotkey (hold Alt+1) typed "1" repeatedly into the message box during
the hold, then pasted the transcript correctly on release. Root cause:
Windows re-fires KEY_DOWN for a held key (key-repeat, ~30/s), and the old
code re-tested keyboard.is_pressed("alt") on every repeat. Alt can read as
released a beat before the char physically is, so once that race fired the
remaining repeats fell through unsuppressed. The fix claims the hold once,
on the first KEY_DOWN with the modifier held, and from then on suppresses
every repeat unconditionally until the matching KEY_UP, regardless of what
is_pressed() reports mid-hold.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main


class _FakeRecorder:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1
        return None


@pytest.fixture
def hotkey_env(monkeypatch):
    """Reset the module-level hotkey state and neutralise real hardware
    seams (recorder, tray, overlay, is_pressed, the clock) for every test."""
    monkeypatch.setattr(main, "_hotkey_claimed", False)
    monkeypatch.setattr(main, "_recording_active", False)
    monkeypatch.setattr(main, "_press_start_time", 0.0)
    monkeypatch.setattr(main, "_last_tap_release_time", 0.0)
    monkeypatch.setattr(main, "_session_active", False)
    monkeypatch.setattr(main, "_paused", False)
    monkeypatch.setattr(main, "_tray", None)
    monkeypatch.setattr(main, "_overlay", None)

    fake_recorder = _FakeRecorder()
    monkeypatch.setattr(main, "_recorder", fake_recorder)

    modifier_state = SimpleNamespace(held=True)
    monkeypatch.setattr(main.keyboard, "is_pressed", lambda key: modifier_state.held)

    # Deterministic clock well clear of the 0.0 sentinel used to seed
    # _last_tap_release_time, so no test can accidentally look like a
    # double-tap of the process start.
    clock = SimpleNamespace(t=1000.0)

    def _monotonic():
        clock.t += 0.01
        return clock.t

    monkeypatch.setattr(main.time, "monotonic", _monotonic)

    event = SimpleNamespace(name=main.settings.hotkey, scan_code=0)
    return SimpleNamespace(recorder=fake_recorder, modifier=modifier_state, event=event, clock=clock)


def test_bare_tap_without_modifier_passes_through(hotkey_env):
    """A press with the modifier not held is a bare tap: it must not be
    claimed, and both press and release let the raw char through."""
    hotkey_env.modifier.held = False

    assert main._on_alt1_press(hotkey_env.event) is True
    assert main._hotkey_claimed is False
    assert main._on_alt1_release(hotkey_env.event) is True
    assert hotkey_env.recorder.start_calls == 0


def test_claimed_hold_starts_recording_and_claims(hotkey_env):
    """The first KEY_DOWN with the modifier held claims the hold, starts
    the recorder, and suppresses the keystroke."""
    assert main._on_alt1_press(hotkey_env.event) is False
    assert main._hotkey_claimed is True
    assert hotkey_env.recorder.start_calls == 1


def test_repeat_downs_suppressed_even_if_modifier_reads_released(hotkey_env):
    """Regression for the reported leak: once claimed, Windows key-repeat
    KEY_DOWNs must be suppressed unconditionally, even if is_pressed(modifier)
    flips to False mid-hold (the observed Alt/char release race)."""
    assert main._on_alt1_press(hotkey_env.event) is False
    assert hotkey_env.recorder.start_calls == 1

    # Simulate Alt reading as released while the char is still physically
    # held down and Windows keeps re-firing repeat KEY_DOWN events.
    hotkey_env.modifier.held = False
    for _ in range(20):
        assert main._on_alt1_press(hotkey_env.event) is False

    assert hotkey_env.recorder.start_calls == 1
    assert main._hotkey_claimed is True


def test_release_clears_claim_and_stops_recording(hotkey_env):
    """The release of a claimed hold is suppressed and clears the claim so
    the next bare tap of the char passes through normally."""
    main._on_alt1_press(hotkey_env.event)
    hotkey_env.modifier.held = False  # mid-hold race, as in the bug report
    main._on_alt1_press(hotkey_env.event)

    assert main._on_alt1_release(hotkey_env.event) is False
    assert main._hotkey_claimed is False
    assert hotkey_env.recorder.stop_calls == 1

    # The very next bare press of "1" (no Alt) must type normally again.
    assert main._on_alt1_press(hotkey_env.event) is True
    assert main._hotkey_claimed is False


def test_double_tap_still_enters_session_mode(hotkey_env, monkeypatch):
    """Two short claimed taps within the double-tap window must still
    toggle session mode: the sticky-claim change must not disturb the
    existing short_tap/double_tap detection in _on_alt1_release."""
    toggles = []
    monkeypatch.setattr(main, "_on_session_toggle", lambda: toggles.append(1))

    clock = hotkey_env.clock
    monkeypatch.setattr(main.time, "monotonic", lambda: clock.t)

    short_tap_s = (main._SHORT_TAP_MAX_MS / 1000) / 2
    double_tap_gap_s = (main._DOUBLE_TAP_WINDOW_MS / 1000) / 2

    main._on_alt1_press(hotkey_env.event)
    clock.t += short_tap_s
    main._on_alt1_release(hotkey_env.event)

    clock.t += double_tap_gap_s
    main._on_alt1_press(hotkey_env.event)
    clock.t += short_tap_s
    main._on_alt1_release(hotkey_env.event)

    assert toggles == [1]
