"""test_transcribe.py — regression tests for transcribe.py.

Covers the Groq HTTP seam (the `language` request parameter, mocked so no
network call ever happens) and the hallucination/tail-trim filters, run
against real garbled Persian utterances captured from a production log.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import transcribe
from settings import DictationSettings

_FIXTURE = Path(__file__).parent / "fixtures" / "garbage_bursts.json"


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _make_wav(tmp_path) -> Path:
    wav_path = tmp_path / "burst.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")
    return wav_path


def test_language_param_present_when_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "settings", DictationSettings(dictation_language="en"))
    captured = {}

    def fake_post(url, headers, files, data, timeout):
        captured["data"] = data
        return _FakeResponse({"text": "hello there", "language": "en"})

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    transcribe.transcribe(_make_wav(tmp_path), api_key="fake-key")
    assert captured["data"].get("language") == "en"


def test_language_param_absent_when_auto(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "settings", DictationSettings(dictation_language="auto"))
    captured = {}

    def fake_post(url, headers, files, data, timeout):
        captured["data"] = data
        return _FakeResponse({"text": "hello there", "language": "en"})

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    transcribe.transcribe(_make_wav(tmp_path), api_key="fake-key")
    assert "language" not in captured["data"]


def _garbage_bursts() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "entry", _garbage_bursts(), ids=lambda e: f"log-line-{e['source_line']}"
)
def test_real_persian_utterances_never_flagged_as_hallucination(entry):
    # These are real dictated Persian utterances pulled from a production
    # log, including one with a doubled word ("دیگه دیگه"). _NOISE_PATTERNS
    # is an English-only token list, so none of these should ever be
    # dropped as hallucination noise or have their tail trimmed.
    raw = entry["transcript_raw"]
    assert transcribe._is_hallucination(raw) is False
    assert transcribe._trim_hallucination_tail(raw) == raw
