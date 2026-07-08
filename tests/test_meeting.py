"""test_meeting.py: regression tests for meeting.py.

No real audio and no network: MeetingRecorder's `_make_stream` is patched to
a no-op fake stream, and both transcribe() and the Groq chat call are
monkeypatched. Covers chunk ordering, the "[inaudible segment]" fallback for
a failed chunk, markdown assembly (summary present and unavailable), and
summarise_transcript's failure path.
"""

from __future__ import annotations

import time

import meeting
from meeting import MeetingRecorder, build_markdown, summarise_transcript


class _FakeStream:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _make_recorder(tmp_path, monkeypatch) -> MeetingRecorder:
    monkeypatch.setattr(MeetingRecorder, "_make_stream", lambda self: _FakeStream())
    recorder = MeetingRecorder(
        api_key="fake-key",
        chunk_seconds=9999,  # large enough that the rollover timer never fires in a test
        session_dir=tmp_path / "session",
    )
    return recorder


def test_chunk_ordering_preserved_and_failed_chunk_becomes_inaudible(tmp_path, monkeypatch):
    call_index = {"n": 0}

    def fake_transcribe(wav_path, api_key):
        n = call_index["n"]
        call_index["n"] += 1
        if n == 1:
            raise RuntimeError("groq unreachable")
        return f"chunk {n} text", "en"

    monkeypatch.setattr(meeting, "transcribe", fake_transcribe)

    recorder = _make_recorder(tmp_path, monkeypatch)
    recorder.start()

    # Simulate three chunks of captured audio, rolled over manually (the real
    # timer is disarmed via the large chunk_seconds above).
    for _ in range(3):
        recorder._frames.append(b"\x00\x01" * 320)
        recorder._finish_current_chunk()

    transcripts = recorder.stop()

    assert transcripts == ["chunk 0 text", "[inaudible segment]", "chunk 2 text"]


def test_partial_transcript_file_written_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting, "transcribe", lambda wav_path, api_key: ("hello", "en"))
    recorder = _make_recorder(tmp_path, monkeypatch)
    recorder.start()
    recorder._frames.append(b"\x00\x01" * 320)
    recorder._finish_current_chunk()
    recorder.stop()

    partial = recorder.session_dir / "transcript.partial.txt"
    assert partial.exists()
    assert partial.read_text(encoding="utf-8") == "hello"


def test_build_markdown_with_summary():
    started = time.localtime(1_720_000_000)
    md = build_markdown(started, 125.0, ["First chunk.", "Second chunk."], "- Point one\n- Point two")
    assert "# Meeting notes:" in md
    assert "2m 5s" in md
    assert "## Summary" in md
    assert "- Point one" in md
    assert "## Transcript" in md
    assert "First chunk." in md
    assert "Second chunk." in md
    assert "Summary unavailable" not in md


def test_build_markdown_summary_unavailable():
    started = time.localtime(1_720_000_000)
    md = build_markdown(started, 10.0, ["Only chunk."], "")
    assert "Summary unavailable. Full transcript follows." in md
    assert "Only chunk." in md


def test_summarise_transcript_success(monkeypatch):
    monkeypatch.setattr(
        meeting.requests, "post", lambda *a, **kw: _FakeResponse("- Bullet summary")
    )
    result = summarise_transcript("some transcript text", "fake-key")
    assert result == "- Bullet summary"


def test_summarise_transcript_failure_returns_empty_string(monkeypatch):
    def raise_error(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(meeting.requests, "post", raise_error)
    result = summarise_transcript("some transcript text", "fake-key")
    assert result == ""
