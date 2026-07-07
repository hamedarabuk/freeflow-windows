"""test_overlay_processing.py — regression tests for the "processing Ns"
live elapsed-time indicator on the floating overlay.

overlay.Overlay.__init__ does no Tk/CTk instantiation (that only happens in
run()/_build()), so the state-machine side of set_state() is testable
headlessly with self._root left as None. Label-rendering behaviour that
needs a live CTk widget tree is not covered here.
"""

from __future__ import annotations

import time

import overlay


def _make_overlay() -> overlay.Overlay:
    noop = lambda *a, **k: None
    return overlay.Overlay(
        on_pause_toggle=noop,
        on_force_mode=noop,
        on_set_translate=noop,
        on_quit=noop,
        get_auto_mode=lambda: "polished",
        get_translate=lambda: False,
        get_forced=lambda: None,
        get_detected_language=lambda: "",
        get_audio_level=lambda: 0.0,
    )


def test_entering_processing_records_timestamp():
    ov = _make_overlay()
    assert ov._processing_started is None
    ov.set_state("processing")
    assert ov._processing_started is not None
    assert time.monotonic() - ov._processing_started < 1.0


def test_leaving_processing_clears_timestamp():
    ov = _make_overlay()
    ov.set_state("processing")
    assert ov._processing_started is not None
    ov.set_state("idle")
    assert ov._processing_started is None


def test_other_states_never_set_timestamp():
    ov = _make_overlay()
    for state in ("idle", "recording", "session", "paused"):
        ov.set_state(state)
        assert ov._processing_started is None


def test_re_entering_processing_starts_a_fresh_clock():
    ov = _make_overlay()
    ov.set_state("processing")
    first = ov._processing_started
    ov.set_state("idle")
    time.sleep(0.05)
    ov.set_state("processing")
    second = ov._processing_started
    assert second is not None
    assert second > first
