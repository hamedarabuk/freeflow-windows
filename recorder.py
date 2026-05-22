"""
recorder.py — push-to-talk WAV capture via sounddevice.

Accumulates frames while the key is held, writes a WAV tempfile on release.
Returns None if the recording is under 250ms (accidental tap).

Optional `level_callback(rms_float)` fires from the audio thread every audio
buffer (~31 Hz at 16kHz with default blocksize). Used by the overlay to draw
a live equaliser/level meter during recording.
"""

from __future__ import annotations

import io
import time
import wave
from pathlib import Path
from typing import Callable, Optional

import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16_000
CHANNELS = 1
MIN_DURATION_MS = 250


class Recorder:
    def __init__(self, level_callback: Optional[Callable[[float], None]] = None) -> None:
        self._frames: list[bytes] = []
        self._start_time: Optional[float] = None
        self._stream: Optional[sd.InputStream] = None
        self._level_callback = level_callback

    def start(self) -> None:
        self._frames = []
        self._start_time = time.monotonic()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status) -> None:
        self._frames.append(bytes(indata))
        if self._level_callback is not None:
            try:
                # int16 array; compute normalised RMS (0..1 roughly).
                # Avoid numpy dependency: use the buffer's array.array view.
                # sounddevice typically delivers a 2-D numpy array, which
                # supports the mean/power ops directly. Fall back gracefully.
                arr = indata
                if hasattr(arr, "astype"):
                    sq = (arr.astype("float32") ** 2).mean()
                    rms = float(sq ** 0.5) / 32768.0
                else:
                    rms = 0.0
                self._level_callback(rms)
            except Exception:
                # Never let the level callback break recording.
                pass

    def stop(self) -> Optional[Path]:
        if self._stream is None:
            return None
        self._stream.stop()
        self._stream.close()
        self._stream = None

        if self._start_time is None:
            return None

        elapsed_ms = (time.monotonic() - self._start_time) * 1000
        if elapsed_ms < MIN_DURATION_MS:
            return None

        audio_bytes = b"".join(self._frames)
        if not audio_bytes:
            return None

        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, prefix="dictation_"
        )
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_bytes)
        return Path(tmp.name)
