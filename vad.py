"""
vad.py — Voice Activity Detection-based session recording.

Hold-to-talk (Alt+1) is the primary path. Session mode is opt-in via
double-tap: continuous mic, VAD frame-by-frame, each detected speech
burst transcribed and pasted immediately.

State machine (per frame):
  silent ----speech for 300ms----> recording
  recording --silence for 800ms--> finalise burst -> dispatch -> silent

Pre-roll buffer (200ms) is prepended to each burst so the first word
isn't clipped off when speech is detected.

Uses webrtcvad (via webrtcvad-wheels on Windows). Frames are exactly
20ms at 16kHz mono int16 (320 samples per frame, 640 bytes).
"""

from __future__ import annotations

import collections
import logging
import tempfile
import wave
from pathlib import Path
from typing import Callable, Optional

import sounddevice as sd

try:
    import webrtcvad
    _HAVE_VAD = True
except ImportError:
    _HAVE_VAD = False

from settings import settings

SAMPLE_RATE = 16_000
FRAME_DURATION_MS      = settings.vad_frame_duration_ms
FRAME_SAMPLES          = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)   # 320
FRAME_BYTES            = FRAME_SAMPLES * 2                              # 640 (int16)

VAD_AGGRESSIVENESS     = settings.vad_aggressiveness
SPEECH_FRAMES_TO_START = settings.vad_speech_frames_to_start
SILENCE_FRAMES_TO_END  = settings.vad_silence_frames_to_end
PRE_ROLL_FRAMES        = settings.vad_pre_roll_frames
MIN_BURST_FRAMES       = settings.vad_min_burst_frames
MAX_BURST_FRAMES       = settings.vad_max_burst_frames

log = logging.getLogger(__name__)


def is_available() -> bool:
    return _HAVE_VAD


class SessionManager:
    """Continuous VAD-driven recorder. Calls on_burst(wav_path) for every
    finalised speech burst."""

    def __init__(
        self,
        on_burst: Callable[[Path], None],
        level_callback: Optional[Callable[[float], None]] = None,
    ) -> None:
        if not _HAVE_VAD:
            raise RuntimeError(
                "webrtcvad is not installed. Run: pip install webrtcvad-wheels"
            )
        self._on_burst = on_burst
        self._level_callback = level_callback
        self._vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self._stream: Optional[sd.InputStream] = None
        self._pre_roll: collections.deque = collections.deque(maxlen=PRE_ROLL_FRAMES)
        self._burst_frames: list[bytes] = []
        self._in_speech = False
        self._speech_frame_count = 0
        self._silence_frame_count = 0

    def start(self) -> None:
        if self._stream is not None:
            return
        self._reset_state()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            callback=self._callback,
        )
        self._stream.start()
        log.info("Session VAD stream started (aggressiveness=%d)", VAD_AGGRESSIVENESS)

    def stop(self) -> None:
        if self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None
        # Finalise any in-progress burst before exiting.
        if self._in_speech and len(self._burst_frames) >= MIN_BURST_FRAMES:
            self._finalise_burst()
        self._reset_state()
        log.info("Session VAD stream stopped")

    def _reset_state(self) -> None:
        self._pre_roll.clear()
        self._burst_frames = []
        self._in_speech = False
        self._speech_frame_count = 0
        self._silence_frame_count = 0

    def _callback(self, indata, frames, time_info, status) -> None:
        # sounddevice should deliver exactly FRAME_SAMPLES given blocksize,
        # but guard defensively for the off-by-one case at stream end.
        if frames != FRAME_SAMPLES:
            return
        frame_bytes = bytes(indata)

        # Audio level for the gadget equaliser.
        if self._level_callback is not None:
            try:
                arr = indata
                if hasattr(arr, "astype"):
                    rms = float(((arr.astype("float32") ** 2).mean()) ** 0.5) / 32768.0
                    self._level_callback(rms)
            except Exception:
                pass

        # VAD verdict.
        try:
            is_speech = self._vad.is_speech(frame_bytes, SAMPLE_RATE)
        except Exception:
            is_speech = False

        if self._in_speech:
            self._burst_frames.append(frame_bytes)
            if is_speech:
                self._silence_frame_count = 0
            else:
                self._silence_frame_count += 1
                if (
                    self._silence_frame_count >= SILENCE_FRAMES_TO_END
                    or len(self._burst_frames) >= MAX_BURST_FRAMES
                ):
                    if len(self._burst_frames) >= MIN_BURST_FRAMES:
                        self._finalise_burst()
                    self._reset_state()
        else:
            # Outside a burst: keep pre-roll fresh and watch for confirmed speech.
            self._pre_roll.append(frame_bytes)
            if is_speech:
                self._speech_frame_count += 1
                if self._speech_frame_count >= SPEECH_FRAMES_TO_START:
                    self._in_speech = True
                    # Seed the burst with the pre-roll so the first word survives.
                    self._burst_frames = list(self._pre_roll)
                    self._silence_frame_count = 0
            else:
                self._speech_frame_count = 0

    def _finalise_burst(self) -> None:
        audio = b"".join(self._burst_frames)
        tmp = tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, prefix="dictation_session_"
        )
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio)
            path = Path(tmp.name)
            log.info(
                "Session burst finalised: %d frames (%.1fs)",
                len(self._burst_frames),
                len(self._burst_frames) * FRAME_DURATION_MS / 1000,
            )
            try:
                self._on_burst(path)
            except Exception as exc:
                log.warning("on_burst handler raised: %s", exc)
        except Exception as exc:
            log.warning("Failed to write session burst WAV: %s", exc)
