"""
transcribe.py — Groq Whisper-large-v3 transcription client.

Uses multipart/form-data upload via requests. No openai SDK.
Returns (transcript_text, detected_language).

Biases Whisper toward known terms via the prompt parameter (dictionary.py)
and applies post-transcription substitutions for known mis-hearings.
"""

from __future__ import annotations

import logging
import requests
from pathlib import Path

from dictionary import get_terms_prompt, apply_substitutions

GROQ_AUDIO_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
TIMEOUT_S = 30

_NO_SPEECH_PROB_CEILING = 0.7

# Per-segment thresholds (OpenAI's published heuristics for catching
# hallucinated segments inside otherwise-valid transcripts):
#   no_speech_prob   >= 0.6  -> silence
#   compression_ratio>= 2.4  -> repetition loop
#   avg_logprob      <= -1.5 -> very low model confidence
_SEG_NO_SPEECH = 0.6
_SEG_COMPRESSION = 2.4
_SEG_LOGPROB = -1.5

log = logging.getLogger(__name__)


def _is_hallucination(text: str) -> bool:
    """Detect Whisper's classic silence/noise hallucination patterns."""
    if not text:
        return False
    tokens = text.split()
    n = len(tokens)
    if n < 2:
        return False
    lower = [t.lower().strip(".,;:!?\"'()[]{}") for t in tokens]
    if n == 2 and lower[0] and lower[0] == lower[1] and len(lower[0]) >= 8:
        return True
    if n < 3:
        return False
    for i in range(n - 2):
        if lower[i] and lower[i] == lower[i + 1] == lower[i + 2]:
            return True
    if n <= 6:
        for i in range(n - 1):
            if lower[i] and lower[i] == lower[i + 1] and len(lower[i]) > 5:
                return True
    if n >= 4 and lower[0] == lower[2] and lower[1] == lower[3] and lower[0]:
        return True
    return False


def _avg_no_speech_prob(payload: dict) -> float:
    segs = payload.get("segments") or []
    probs = [
        s.get("no_speech_prob")
        for s in segs
        if isinstance(s.get("no_speech_prob"), (int, float))
    ]
    if not probs:
        return 0.0
    return sum(probs) / len(probs)


def _filter_segments(payload: dict) -> str:
    """Walk verbose_json segments and drop hallucinated ones individually.

    Whisper's tail-hallucination failure mode (valid prefix then garbage
    proper-noun list or token loop) usually lands in one or two trailing
    segments with elevated compression_ratio and depressed avg_logprob.
    Per-segment filtering keeps the valid prefix and drops only the
    suspect tail."""
    segs = payload.get("segments") or []
    if not segs:
        return (payload.get("text") or "").strip()
    kept: list[str] = []
    dropped = 0
    for s in segs:
        ns = s.get("no_speech_prob")
        cr = s.get("compression_ratio")
        ap = s.get("avg_logprob")
        if isinstance(ns, (int, float)) and ns >= _SEG_NO_SPEECH:
            dropped += 1
            continue
        if isinstance(cr, (int, float)) and cr >= _SEG_COMPRESSION:
            dropped += 1
            continue
        if isinstance(ap, (int, float)) and ap <= _SEG_LOGPROB:
            dropped += 1
            continue
        t = (s.get("text") or "").strip()
        if t:
            kept.append(t)
    if dropped:
        log.info("Dropped %d hallucinated segment(s) of %d total", dropped, len(segs))
    return " ".join(kept).strip()


def _trim_hallucination_tail(text: str) -> str:
    """Trim Whisper's tail-loop hallucinations off otherwise-valid text.

    When per-segment filtering can't catch a hallucination, look at the
    last few tokens for an obvious repeat ("Sadegh, Sadegh.") and walk
    back to the previous sentence terminator. Conservative: only trims
    if at least half the text survives."""
    if not text:
        return text
    tokens = text.split()
    n = len(tokens)
    if n < 5:
        return text
    tail = [t.lower().strip(".,;:!?\"'()[]{}") for t in tokens[-4:]]
    has_loop = False
    for i in range(len(tail) - 1):
        if tail[i] and tail[i] == tail[i + 1] and len(tail[i]) > 3:
            has_loop = True
            break
    if not has_loop:
        return text
    cuts = []
    for marker in (". ", "! ", "? "):
        idx = text.rfind(marker)
        if idx >= 0:
            cuts.append(idx + len(marker))
    if not cuts:
        return text
    cut = max(cuts)
    trimmed = text[:cut].rstrip()
    if not trimmed or len(trimmed) < len(text) * 0.5:
        return text
    log.info("Trimmed tail hallucination from transcript")
    return trimmed


def transcribe(wav_path: Path, api_key: str) -> tuple[str, str]:
    """Upload WAV to Groq Whisper and return (text, language).

    Returns an empty text when the burst is silence/noise so the caller
    skips the cleanup + paste round-trip."""
    data = {
        "model": MODEL,
        "response_format": "verbose_json",
    }
    prompt = get_terms_prompt()
    if prompt:
        data["prompt"] = prompt
    with open(wav_path, "rb") as f:
        response = requests.post(
            GROQ_AUDIO_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (wav_path.name, f, "audio/wav")},
            data=data,
            timeout=TIMEOUT_S,
        )
    response.raise_for_status()
    payload = response.json()
    language: str = payload.get("language", "en")

    avg_nsp = _avg_no_speech_prob(payload)
    if avg_nsp >= _NO_SPEECH_PROB_CEILING:
        log.info(
            "Dropped burst: avg no_speech_prob=%.2f, text=%r",
            avg_nsp, payload.get("text", ""),
        )
        return "", language

    text = _filter_segments(payload)
    text = _trim_hallucination_tail(text)

    if _is_hallucination(text):
        log.info("Dropped burst: hallucination pattern detected, text=%r", text)
        return "", language

    text = apply_substitutions(text)
    return text, language
