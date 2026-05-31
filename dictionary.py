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
import os
import re
from pathlib import Path

from settings import settings

DICT_FILE     = Path(__file__).resolve().parent / "dictionary.json"
EXAMPLE_FILE  = Path(__file__).resolve().parent / "dictionary.json.example"

_MAX_PROMPT_CHARS = settings.max_prompt_chars

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
            escaped = re.escape(src)
            lb = r"\b" if src and re.match(r"\w", src[0]) else ""
            rb = r"\b" if src and re.match(r"\w", src[-1]) else ""
            pattern = re.compile(lb + escaped + rb, re.IGNORECASE)
            out = pattern.sub(dst, out)
        except Exception:
            continue
    return out


def load_substitutions() -> dict:
    """Current say->write substitutions map (a fresh copy, safe to mutate)."""
    data = _load()
    return dict(data["substitutions"])


def save_substitutions(mapping: dict) -> None:
    """Replace ONLY the substitutions in dictionary.json, preserving all other
    top-level keys (terms, comments, etc.). Writes atomically (temp file in the
    same directory, then os.replace) and refreshes the module cache so the
    change is live on the next dictation without an mtime race."""
    # Start from the real dictionary.json if it exists, else the example, so a
    # first-time save still seeds terms and any comment keys.
    src = _source()
    try:
        existing = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
        if not isinstance(existing, dict):
            existing = {}
    except Exception as exc:
        log.warning("Could not read %s before save, starting fresh: %s", src, exc)
        existing = {}

    clean = {str(k): str(v) for k, v in mapping.items() if str(k).strip()}
    existing["substitutions"] = clean

    tmp = DICT_FILE.with_name(DICT_FILE.name + ".tmp")
    text = json.dumps(existing, indent=2, ensure_ascii=False)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, DICT_FILE)

    # Refresh the cache directly from what we just wrote (no mtime read race):
    # _load filters terms/substitutions to the same normalised shape.
    _cache["data"] = {
        "terms": [
            str(t) for t in existing.get("terms", []) if str(t).strip()
        ],
        "substitutions": clean,
    }
    try:
        _cache["mtime"] = DICT_FILE.stat().st_mtime
    except Exception:
        _cache["mtime"] = -1.0
    log.info("Saved %d substitutions to %s", len(clean), DICT_FILE.name)
