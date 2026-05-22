"""
snippets.py — voice shortcuts.

Loads `snippets.json` (falls back to `snippets.json.example`). Cached
by file mtime so edits take effect immediately.

If the dictation transcript (after dictionary substitutions) matches a
cue exactly (case-insensitive, whitespace trimmed), expand_snippet()
returns the expansion. The dispatcher then pastes that and skips LLM
cleanup entirely.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

SNIP_FILE    = Path(__file__).resolve().parent / "snippets.json"
EXAMPLE_FILE = Path(__file__).resolve().parent / "snippets.json.example"

log = logging.getLogger(__name__)

_cache: dict = {"mtime": -1.0, "snippets": {}}


def _source() -> Path:
    return SNIP_FILE if SNIP_FILE.exists() else EXAMPLE_FILE


def _normalise(key: str) -> str:
    # Lowercase, collapse whitespace, strip trailing punctuation that
    # speech-to-text often appends (period, question mark, comma).
    s = " ".join(key.lower().split())
    while s and s[-1] in ".,;:!?":
        s = s[:-1]
    return s


def _load() -> dict:
    src = _source()
    if not src.exists():
        return _cache["snippets"]
    try:
        mtime = src.stat().st_mtime
    except Exception:
        return _cache["snippets"]
    if mtime == _cache["mtime"]:
        return _cache["snippets"]
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
        items = raw.get("snippets", {})
        snippets = {
            _normalise(str(k)): str(v)
            for k, v in items.items()
            if str(k).strip()
        }
        _cache["mtime"] = mtime
        _cache["snippets"] = snippets
        log.info(
            "Snippets loaded from %s: %d cues",
            src.name, len(snippets),
        )
        return snippets
    except Exception as exc:
        log.warning("Failed to load snippets %s: %s", src, exc)
        return _cache["snippets"]


def expand_snippet(text: str) -> Optional[str]:
    """Return the expansion if `text` matches a cue, else None."""
    snippets = _load()
    if not snippets or not text:
        return None
    key = _normalise(text)
    return snippets.get(key)
