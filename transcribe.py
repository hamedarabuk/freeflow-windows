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

# Whisper's verbose_json returns a per-segment `no_speech_prob`. If the
# average across all segments crosses this threshold the burst was almost
# certainly silence or noise and Whisper hallucinated a transcript. Drop.
_NO_SPEECH_PROB_CEILING = 0.7

log = logging.getLogger(__name__)


def _is_hallucination(text: str) -> bool:
    """Detect Whisper's classic silence/noise hallucination patterns.

    Whisper trained on YouTube text emits a known repertoire of garbage
    when fed silence, breath, or low-SNR audio: repeated tokens, looping
    short phrases, or template strings ("Thank you for watching" etc.).
    A pragmatic heuristic catches most of these without needing an LLM:

      - three identical consecutive tokens (case-insensitive)
      - two identical tokens forming the whole transcript when long enough
      - two identical consecutive long tokens in a short transcript
      - a two-token loop like "X Y X Y"
    """
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
    text: str = payload.get("text", "").strip()
    language: str = payload.get("language", "en")
    avg_nsp = _avg_no_speech_prob(payload)
    if avg_nsp >= _NO_SPEECH_PROB_CEILING:
        log.info("Dropped burst: avg no_speech_prob=%.2f, text=%r", avg_nsp, text)
        return "", language
    if _is_hallucination(text):
        log.info("Dropped burst: hallucination pattern detected, text=%r", text)
        return "", language
    text = apply_substitutions(text)
    return text, language
