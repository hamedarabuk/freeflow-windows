"""
transcribe.py — Groq Whisper-large-v3 transcription client.

Uses multipart/form-data upload via requests. No openai SDK.
Returns (transcript_text, detected_language).

Biases Whisper toward known terms via the prompt parameter (dictionary.py)
and applies post-transcription substitutions for known mis-hearings.
"""

from __future__ import annotations

import requests
from pathlib import Path

from dictionary import get_terms_prompt, apply_substitutions

GROQ_AUDIO_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
TIMEOUT_S = 30


def transcribe(wav_path: Path, api_key: str) -> tuple[str, str]:
    """Upload WAV to Groq Whisper and return (text, language)."""
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
    text = apply_substitutions(text)
    language: str = payload.get("language", "en")
    return text, language
