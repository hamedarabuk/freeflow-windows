"""test_dispatch.py: unit tests for the _dispatch pipeline in main.py.

The dispatch pipeline routes one recorded burst through ordered stages and
funnels every terminal outcome through a single _finalise. These tests mock
at the module seams (main.transcribe, main.clean, main.paste_text, and the
match helpers) so no network call, clipboard write, or real %APPDATA% write
ever happens. They assert the branch taken and that _finalise runs exactly
once per dispatch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Neutralise every dispatch seam and record what the pipeline calls.

    Defaults route a plain utterance down the cleanup path: no capture, voice
    command, snippet or inline formatting matches. Individual tests override
    the transcribe/clean returns or a single match helper as needed."""
    calls = SimpleNamespace(events=[], paste=[], append=[], finalise=[])
    calls.transcribe_return = ("hello world", "en")
    calls.clean_return = ("Hello world.", False)

    # Routing + timing: keep foreground detection off the real OS.
    monkeypatch.setattr(main, "_get_foreground_info", lambda: ("", ""))

    # Match helpers default to "no match" so the burst reaches cleanup.
    monkeypatch.setattr(main, "_match_capture_command", lambda text: None)
    monkeypatch.setattr(main, "_match_voice_command", lambda text: None)
    monkeypatch.setattr(main, "expand_snippet", lambda text: None)
    monkeypatch.setattr(main, "_split_inline_formatting", lambda text: None)
    monkeypatch.setattr(main, "apply_substitutions", lambda text: text)

    def fake_transcribe(wav, api_key):
        calls.events.append("transcribe")
        return calls.transcribe_return

    def fake_clean(transcript, mode, api_key, translate_to_english=False):
        calls.events.append("clean")
        return calls.clean_return

    def fake_paste(text):
        calls.events.append("paste")
        calls.paste.append(text)

    def fake_append(**kwargs):
        calls.events.append("append")
        calls.append.append(kwargs)

    def spy_finalise(outcome):
        calls.events.append("finalise")
        calls.finalise.append(outcome)

    monkeypatch.setattr(main, "transcribe", fake_transcribe)
    monkeypatch.setattr(main, "clean", fake_clean)
    monkeypatch.setattr(main, "paste_text", fake_paste)
    monkeypatch.setattr(main, "append", fake_append)
    monkeypatch.setattr(main, "_finalise", spy_finalise)

    calls.wav = tmp_path / "burst.wav"
    calls.wav.write_bytes(b"RIFF....WAVEfmt ")
    return calls


def test_plain_utterance_flows_transcribe_clean_paste(env):
    """(a) A plain utterance runs transcribe -> cleanup -> paste and
    finalises exactly once."""
    main._dispatch(env.wav)

    assert "transcribe" in env.events
    assert "clean" in env.events
    assert "paste" in env.events
    assert env.events.index("transcribe") < env.events.index("clean")
    assert env.events.index("clean") < env.events.index("paste")
    assert env.paste == ["Hello world."]
    assert env.events.count("finalise") == 1
    assert env.finalise[0].kind == "cleaned_paste"
    assert env.finalise[0].fallback_badge is False


def test_voice_command_short_circuits_before_cleanup(env, monkeypatch):
    """(b) A voice-command utterance short-circuits before cleanup and still
    finalises exactly once."""
    monkeypatch.setattr(main, "_match_voice_command", lambda text: ("text", "SIGNATURE"))

    main._dispatch(env.wav)

    assert "clean" not in env.events
    assert env.paste == ["SIGNATURE"]
    assert env.events.count("finalise") == 1
    assert env.finalise[0].kind == "voice_command"


def test_guard_fallback_to_raw_sets_raw_badge(env):
    """(c) When cleanup falls back to the raw transcript, the outcome carries
    the RAW fallback badge and the raw text is pasted."""
    env.clean_return = ("hello world", True)

    main._dispatch(env.wav)

    assert "clean" in env.events
    assert env.paste == ["hello world"]
    assert env.events.count("finalise") == 1
    assert env.finalise[0].kind == "cleaned_paste"
    assert env.finalise[0].fallback_badge is True


def test_empty_transcript_finalises_without_paste(env):
    """(d) An empty transcript finalises without cleanup or paste."""
    env.transcribe_return = ("", "en")

    main._dispatch(env.wav)

    assert "clean" not in env.events
    assert "paste" not in env.events
    assert env.paste == []
    assert env.events.count("finalise") == 1
    assert env.finalise[0].kind == "empty_transcript"
    assert env.finalise[0].fallback_badge is False
