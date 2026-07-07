"""test_latency.py — unit tests for the end-to-end latency instrumentation.

Covers the rolling ms_total window (get_recent_latency_ms) and the
structured "latency ..." INFO line emitted for every completed paste
outcome (cleaned_paste, snippet, voice command). The dispatch seams
(mocking transcribe/clean/paste_text/append) follow the same pattern as
tests/test_dispatch.py.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import main


@pytest.fixture(autouse=True)
def clear_latency_window():
    """Every test starts with an empty rolling window and leaves one
    behind, so state never leaks between test files."""
    main._recent_latency_ms.clear()
    yield
    main._recent_latency_ms.clear()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Neutralise every dispatch seam; defaults route a plain utterance
    down the cleanup path. See tests/test_dispatch.py for the same pattern."""
    calls = SimpleNamespace(paste=[], append=[])
    calls.transcribe_return = ("hello world", "en")
    calls.clean_return = ("Hello world.", False)

    monkeypatch.setattr(main, "_get_foreground_info", lambda: ("", ""))
    monkeypatch.setattr(main, "_match_capture_command", lambda text: None)
    monkeypatch.setattr(main, "_match_voice_command", lambda text: None)
    monkeypatch.setattr(main, "expand_snippet", lambda text: None)
    monkeypatch.setattr(main, "_split_inline_formatting", lambda text: None)
    monkeypatch.setattr(main, "apply_substitutions", lambda text: text)

    def fake_transcribe(wav, api_key):
        return calls.transcribe_return

    def fake_clean(transcript, mode, api_key, translate_to_english=False):
        return calls.clean_return

    def fake_paste(text):
        calls.paste.append(text)

    def fake_append(**kwargs):
        calls.append.append(kwargs)

    monkeypatch.setattr(main, "transcribe", fake_transcribe)
    monkeypatch.setattr(main, "clean", fake_clean)
    monkeypatch.setattr(main, "paste_text", fake_paste)
    monkeypatch.setattr(main, "append", fake_append)

    calls.wav = tmp_path / "burst.wav"
    calls.wav.write_bytes(b"RIFF....WAVEfmt ")
    return calls


def _latency_lines(caplog):
    return [r for r in caplog.records if r.message.startswith("latency ")]


def test_get_recent_latency_ms_empty_when_no_dispatches():
    assert main.get_recent_latency_ms() is None


def test_get_recent_latency_ms_last_and_average():
    for ms in (100, 200, 300):
        main._recent_latency_ms.append(ms)
    assert main.get_recent_latency_ms() == (300, 200)


def test_deque_caps_at_twenty_entries():
    for ms in range(30):
        main._recent_latency_ms.append(ms)
    assert len(main._recent_latency_ms) == 20
    # Oldest ten entries (0..9) dropped, window starts at 10.
    assert main._recent_latency_ms[0] == 10
    assert main._recent_latency_ms[-1] == 29


def test_cleaned_paste_logs_latency_line_and_updates_window(env, caplog):
    caplog.set_level(logging.INFO, logger="dictation.main")

    main._dispatch(env.wav)

    lines = _latency_lines(caplog)
    assert len(lines) == 1
    assert "ms_total=" in lines[0].message
    assert "ms_transcribe=" in lines[0].message
    assert "ms_cleanup=" in lines[0].message
    assert "mode=polished" in lines[0].message
    assert main.get_recent_latency_ms() is not None


def test_snippet_logs_latency_line_with_zero_cleanup(env, caplog, monkeypatch):
    monkeypatch.setattr(main, "expand_snippet", lambda text: "expanded text")
    caplog.set_level(logging.INFO, logger="dictation.main")

    main._dispatch(env.wav)

    lines = _latency_lines(caplog)
    assert len(lines) == 1
    assert "ms_cleanup=0" in lines[0].message
    # Snippet outcomes do not feed the cleaned_paste rolling window.
    assert main.get_recent_latency_ms() is None


def test_voice_command_logs_latency_line_with_zero_cleanup(env, caplog, monkeypatch):
    monkeypatch.setattr(main, "_match_voice_command", lambda text: ("text", "SIGNATURE"))
    caplog.set_level(logging.INFO, logger="dictation.main")

    main._dispatch(env.wav)

    lines = _latency_lines(caplog)
    assert len(lines) == 1
    assert "ms_cleanup=0" in lines[0].message
    assert main.get_recent_latency_ms() is None
