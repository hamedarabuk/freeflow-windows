"""
dictionary.py — Whisper prompt bias + post-transcription substitutions.

Loads `dictionary.json` (falls back to `dictionary.json.example` if the
user hasn't created their own). Cached by file mtime so edits take
effect on the next dictation without a service restart.

Two outputs:
- get_terms_prompt() returns a space-joined string for Whisper's prompt
  parameter, which biases transcription toward known terms.
- apply_substitutions(text) runs case-insensitive find-and-replace on
  the raw transcript to fix known mis-hearings.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

DICT_FILE     = Path(__file__).resolve().parent / "dictionary.json"
EXAMPLE_FILE  = Path(__file__).resolve().parent / "dictionary.json.example"

# Whisper's prompt is limited to ~224 tokens. Cap the joined string
# to roughly 220 characters as a defensive ceiling.
_MAX_PROMPT_CHARS = 220

log = logging.getLogger(__name__)

_cache: dict = {"mtime": -1.0, "data": {"terms": [], "substitutions": {}}}


def _source() -> Path:
    return DICT_FILE if DICT_FILE.exists() else EXAMPLE_FILE


def _load() -> dict:
    src = _source()
    if not src.exists():
        return _cache["data"]
    try:
        mtime = src.stat().st_mtime
    except Exception:
        return _cache["data"]
    if mtime == _cache["mtime"]:
        return _cache["data"]
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
        data = {
            "terms": [str(t) for t in raw.get("terms", []) if str(t).strip()],
            "substitutions": {
                str(k): str(v)
                for k, v in raw.get("substitutions", {}).items()
                if str(k).strip()
            },
        }
        _cache["mtime"] = mtime
        _cache["data"] = data
        log.info(
            "Dictionary loaded from %s: %d terms, %d substitutions",
            src.name, len(data["terms"]), len(data["substitutions"]),
        )
        return data
    except Exception as exc:
        log.warning("Failed to load dictionary %s: %s", src, exc)
        return _cache["data"]


def get_terms_prompt() -> str:
    """Space-joined list of dictionary terms for Whisper's prompt param."""
    data = _load()
    terms = data["terms"]
    if not terms:
        return ""
    prompt = " ".join(terms)
    if len(prompt) > _MAX_PROMPT_CHARS:
        prompt = prompt[:_MAX_PROMPT_CHARS]
    return prompt


def apply_substitutions(text: str) -> str:
    """Case-insensitive whole-word substitutions to fix known mis-hearings."""
    data = _load()
    subs = data["substitutions"]
    if not subs or not text:
        return text
    out = text
    for src, dst in subs.items():
        try:
            pattern = re.compile(re.escape(src), re.IGNORECASE)
            out = pattern.sub(dst, out)
        except Exception:
            continue
    return out
