"""test_offline_fallback.py — regression tests for the faster-whisper offline
transcription fallback.

Covers: (1) the package-not-installed baseline (is_available() False, a
network error propagates exactly as before the feature existed), (2) the
mocked-package path (a network-class error triggers local transcription and
sets the offline flag), (3) that an HTTP error response does NOT trigger the
fallback (Groq is reachable in that case). Follows test_transcribe.py's
seam-mocking style: monkeypatch attributes on the imported modules directly,
no real network or model download.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

import transcribe
import transcribe_local
from settings import DictationSettings


class _FakeResponse:
    def __init__(self, payload: dict = None, http_error: Exception = None):
        self._payload = payload or {}
        self._http_error = http_error

    def raise_for_status(self) -> None:
        if self._http_error is not None:
            raise self._http_error

    def json(self) -> dict:
        return self._payload


def _make_wav(tmp_path) -> Path:
    wav_path = tmp_path / "burst.wav"
    wav_path.write_bytes(b"RIFF....WAVEfmt ")
    return wav_path


def test_is_available_false_when_package_not_installed():
    # faster-whisper is optional and not in requirements.txt; the real
    # import check must return False in this environment.
    assert transcribe_local.is_available() is False


def test_connection_error_propagates_when_package_not_installed(tmp_path, monkeypatch):
    # Baseline: with the optional package absent, a network error must
    # propagate exactly as it did before this feature existed.
    monkeypatch.setattr(transcribe, "settings", DictationSettings())

    def fake_post(url, headers, files, data, timeout):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    with pytest.raises(requests.ConnectionError):
        transcribe.transcribe(_make_wav(tmp_path), api_key="fake-key")
    assert transcribe.last_call_used_offline is False


def test_connection_error_triggers_local_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "settings", DictationSettings())
    monkeypatch.setattr(transcribe.transcribe_local, "is_available", lambda: True)

    def fake_transcribe_local(wav_path, language="auto"):
        return {"text": "hello offline", "language": "en", "segments": []}

    monkeypatch.setattr(
        transcribe.transcribe_local, "transcribe_local", fake_transcribe_local
    )

    def fake_post(url, headers, files, data, timeout):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    text, language = transcribe.transcribe(_make_wav(tmp_path), api_key="fake-key")

    assert text == "hello offline"
    assert language == "en"
    assert transcribe.last_call_used_offline is True


def test_http_error_does_not_trigger_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "settings", DictationSettings())
    monkeypatch.setattr(transcribe.transcribe_local, "is_available", lambda: True)

    called = {"local": False}

    def fake_transcribe_local(wav_path, language="auto"):
        called["local"] = True
        return {"text": "should not be used", "language": "en", "segments": []}

    monkeypatch.setattr(
        transcribe.transcribe_local, "transcribe_local", fake_transcribe_local
    )

    def fake_post(url, headers, files, data, timeout):
        return _FakeResponse(http_error=requests.HTTPError("500 Server Error"))

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    with pytest.raises(requests.HTTPError):
        transcribe.transcribe(_make_wav(tmp_path), api_key="fake-key")

    assert called["local"] is False
    assert transcribe.last_call_used_offline is False
