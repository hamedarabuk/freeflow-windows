"""meeting.py: meeting notes mode v1, mic-only continuous capture.

While active, MeetingRecorder records the microphone in fixed-length wav
chunks (same 16kHz mono int16 settings as recorder.py, no new audio
dependency). Each finished chunk is handed to a single background worker
thread that transcribes it via transcribe.transcribe(); a failed chunk is
logged and marked "[inaudible segment]" rather than aborting the session.
Resolved chunks are also appended to transcript.partial.txt in the session
folder as a crash-safety net.

On stop, the caller (main.py) uses summarise_transcript() plus
build_markdown() (or the write_meeting_notes() convenience wrapper) to
produce the final meeting-notes.md.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import requests
import sounddevice as sd

import cleanup
from paths import user_data_dir
from settings import settings
from transcribe import transcribe

SAMPLE_RATE = 16_000
CHANNELS = 1

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
SUMMARY_TIMEOUT_S = 30.0

SUMMARY_SYSTEM_PROMPT = (
    "Summarise this meeting transcript. Return: a 3-6 bullet summary, "
    "decisions made, and action items with owners where identifiable. "
    "Use British English, no em-dashes, plain markdown."
)

log = logging.getLogger(__name__)


def _new_session_dir() -> Path:
    name = time.strftime("%Y-%m-%d-%H%M")
    return user_data_dir() / "meetings" / name


class MeetingRecorder:
    """Continuous mic recorder for meeting notes.

    Records fixed-length wav chunks to *session_dir* and transcribes each
    one, in order, on a dedicated background thread as soon as it finishes.
    The audio capture itself is isolated behind `_make_stream`, so tests can
    patch that one method to avoid touching real hardware while exercising
    everything else (chunking, transcription, ordering, stop/wait).
    """

    def __init__(
        self,
        api_key: str,
        chunk_seconds: Optional[int] = None,
        session_dir: Optional[Path] = None,
        level_callback: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._api_key = api_key
        self._chunk_seconds = chunk_seconds or settings.meeting_chunk_seconds
        self._session_dir = session_dir or _new_session_dir()
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._level_callback = level_callback

        self._frames: list[bytes] = []
        self._chunk_index = 0
        self._stream = None
        self._chunk_timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()

        self._start_time: Optional[float] = None
        self._started_wall: Optional[time.struct_time] = None

        self._transcripts: list[Optional[str]] = []
        self._transcripts_lock = threading.Lock()
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._pending_done = threading.Event()
        self._pending_done.set()

        self._transcribe_queue: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    @property
    def started(self) -> time.struct_time:
        return self._started_wall or time.localtime()

    def elapsed_seconds(self) -> float:
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time

    def start(self) -> None:
        self._start_time = time.monotonic()
        self._started_wall = time.localtime()
        self._stopped.clear()
        self._worker = threading.Thread(
            target=self._transcribe_worker, name="meeting-transcribe-worker", daemon=True
        )
        self._worker.start()
        self._open_stream()
        self._schedule_chunk_rollover()

    def _make_stream(self):
        """Isolated capture seam. Patch this method in tests to inject a
        fake stream (no hardware, no real audio)."""
        return sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=self._callback,
        )

    def _open_stream(self) -> None:
        self._frames = []
        self._stream = self._make_stream()
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        self._frames.append(bytes(indata))
        if self._level_callback is not None:
            try:
                arr = indata
                if hasattr(arr, "astype"):
                    rms = float(((arr.astype("float32") ** 2).mean()) ** 0.5) / 32768.0
                    self._level_callback(rms)
            except Exception:
                pass

    def _schedule_chunk_rollover(self) -> None:
        self._chunk_timer = threading.Timer(self._chunk_seconds, self._rollover_chunk)
        self._chunk_timer.daemon = True
        self._chunk_timer.start()

    def _rollover_chunk(self) -> None:
        if self._stopped.is_set():
            return
        self._finish_current_chunk()
        self._open_stream()
        self._schedule_chunk_rollover()

    def _finish_current_chunk(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        audio_bytes = b"".join(self._frames)
        self._frames = []
        if not audio_bytes:
            return
        index = self._chunk_index
        self._chunk_index += 1
        chunk_path = self._session_dir / f"chunk-{index:03d}.wav"
        with wave.open(str(chunk_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_bytes)
        with self._transcripts_lock:
            self._transcripts.append(None)
        with self._pending_lock:
            self._pending += 1
            self._pending_done.clear()
        self._transcribe_queue.put((index, chunk_path))

    def _transcribe_worker(self) -> None:
        while True:
            item = self._transcribe_queue.get()
            if item is None:
                return
            index, chunk_path = item
            try:
                text, _language = transcribe(chunk_path, self._api_key)
                text = text.strip() or "[inaudible segment]"
            except Exception as exc:
                log.warning("Meeting chunk %d transcription failed: %s", index, exc)
                text = "[inaudible segment]"
            with self._transcripts_lock:
                self._transcripts[index] = text
                self._write_partial()
            with self._pending_lock:
                self._pending -= 1
                if self._pending <= 0:
                    self._pending_done.set()

    def _write_partial(self) -> None:
        """Called with _transcripts_lock held. Writes the resolved prefix of
        chunks so far, so a crash mid-meeting still leaves a usable transcript."""
        partial_path = self._session_dir / "transcript.partial.txt"
        resolved = [t for t in self._transcripts if t is not None]
        try:
            partial_path.write_text("\n\n".join(resolved), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed to write transcript.partial.txt: %s", exc)

    def stop(self, pending_timeout_s: float = 120.0) -> list[str]:
        """Finish the current chunk, wait (bounded) for pending
        transcriptions, then return the ordered transcript list."""
        self._stopped.set()
        if self._chunk_timer is not None:
            self._chunk_timer.cancel()
            self._chunk_timer = None
        self._finish_current_chunk()
        self._pending_done.wait(timeout=pending_timeout_s)
        self._transcribe_queue.put(None)
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        with self._transcripts_lock:
            return [t if t is not None else "[inaudible segment]" for t in self._transcripts]


def summarise_transcript(text: str, api_key: str) -> str:
    """Summarise a meeting transcript via the Groq chat model. Returns ""
    on any failure (network, HTTP, or parse error)."""
    # Shares cleanup.py's model selection so a model retirement detected on
    # the dictation path benefits meeting summaries in the same session.
    model = cleanup.active_model()
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        **cleanup.model_params(model),
    }
    try:
        response = requests.post(
            GROQ_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=SUMMARY_TIMEOUT_S,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log.warning("Meeting summary failed: %s", exc)
        return ""


def build_markdown(
    started: time.struct_time,
    duration_seconds: float,
    transcript_chunks: list[str],
    summary: str,
) -> str:
    """Assemble meeting-notes.md: title (date/time + duration), summary
    section (or an unavailable note), then the full transcript."""
    title_ts = time.strftime("%Y-%m-%d %H:%M", started)
    minutes = int(duration_seconds // 60)
    seconds = int(duration_seconds % 60)
    lines = [
        f"# Meeting notes: {title_ts} ({minutes}m {seconds}s)",
        "",
        "## Summary",
        "",
        summary if summary else "Summary unavailable. Full transcript follows.",
        "",
        "## Transcript",
        "",
        "\n\n".join(transcript_chunks) if transcript_chunks else "(no speech captured)",
    ]
    return "\n".join(lines) + "\n"


def write_meeting_notes(recorder: MeetingRecorder, api_key: str) -> Path:
    """Stop *recorder*, summarise the transcript, write meeting-notes.md into
    the session folder, and return its path. Called by main.py's meeting
    toggle handler on stop; does not open the file or notify the tray, that
    stays in main.py alongside the rest of its UI-facing side effects."""
    transcript_chunks = recorder.stop()
    full_transcript = "\n\n".join(c for c in transcript_chunks if c)
    summary = summarise_transcript(full_transcript, api_key) if full_transcript.strip() else ""
    duration = recorder.elapsed_seconds()
    markdown = build_markdown(recorder.started, duration, transcript_chunks, summary)
    output_path = recorder.session_dir / "meeting-notes.md"
    output_path.write_text(markdown, encoding="utf-8")
    return output_path
