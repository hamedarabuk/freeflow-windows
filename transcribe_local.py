"""
transcribe_local.py — optional offline transcription fallback via faster-whisper.

Used only by transcribe.py when the Groq Whisper HTTP call fails with a
network-class error (connection error, timeout, DNS) and the optional
faster-whisper package is installed (see requirements-optional.txt).
Strictly optional: is_available() returns False when the package is absent,
and nothing here is imported or run unless the caller reaches for it.

Returns a verbose_json-shaped payload matching the Groq response contract
(text, language, segments with no_speech_prob/compression_ratio/avg_logprob)
so transcribe.py's existing hallucination and tail-trim filters apply to
local transcripts unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from settings import settings

log = logging.getLogger(__name__)

_model: Optional[Any] = None


def is_available() -> bool:
    """True when faster-whisper is importable. Never raises."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model() -> Any:
    """Lazy singleton loader. First call downloads the model (~460MB for
    the default "small" model) and logs once; never touches the network
    or the disk at import time."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info("Loading local Whisper model (first use downloads the model)")
        _model = WhisperModel(
            settings.local_whisper_model, device="cpu", compute_type="int8"
        )
    return _model


def transcribe_local(wav_path: Path, language: str = "auto") -> dict:
    """Transcribe *wav_path* locally and return a verbose_json-shaped payload:
    {"text": str, "language": str, "segments": [{"text", "no_speech_prob",
    "compression_ratio", "avg_logprob"}, ...]}.

    *language* mirrors the Groq path's dictation_language setting: "auto"
    lets faster-whisper detect it per utterance; any other ISO code locks
    it, same as the Groq `language` request parameter.
    """
    model = _get_model()
    lang = (language or "auto").strip().lower()
    kwargs: dict[str, Any] = {}
    if lang and lang != "auto":
        kwargs["language"] = lang

    segments_iter, info = model.transcribe(str(wav_path), **kwargs)

    segments: list[dict] = []
    texts: list[str] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        texts.append(text)
        segments.append({
            "text": text,
            "no_speech_prob": getattr(seg, "no_speech_prob", 0.0),
            "compression_ratio": getattr(seg, "compression_ratio", 0.0),
            "avg_logprob": getattr(seg, "avg_logprob", 0.0),
        })

    detected_language = getattr(info, "language", None) or (
        lang if lang != "auto" else "en"
    )
    return {
        "text": " ".join(t for t in texts if t).strip(),
        "language": detected_language,
        "segments": segments,
    }
