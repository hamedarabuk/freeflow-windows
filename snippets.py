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
import os
from pathlib import Path
from typing import Optional

from backup import backup_if_changed

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
        # Keep the last-good in-memory copy so a corrupted or partially-written
        # file does not silently empty the snippet table mid-session. On first
        # load (no prior good copy) the cache holds an empty dict, which is safe
        # but visible in logs.
        if _cache["mtime"] == -1.0:
            log.error(
                "SNIPPETS LOAD FAILED on first read (%s: %s). "
                "Voice shortcuts disabled until the file is fixed.",
                src, exc,
            )
        else:
            log.warning(
                "Failed to reload snippets %s: %s — keeping previous copy.",
                src, exc,
            )
        return _cache["snippets"]


def expand_snippet(text: str) -> Optional[str]:
    """Return the expansion if `text` matches a cue, else None."""
    snippets = _load()
    if not snippets or not text:
        return None
    key = _normalise(text)
    return snippets.get(key)


def load_snippets() -> dict:
    """Current trigger->expansion map for the editor, with the triggers in
    their ORIGINAL casing as written in the file (not the normalised cache,
    which is lower-cased for matching). A fresh copy, safe to mutate."""
    src = _source()
    if not src.exists():
        return {}
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
        items = raw.get("snippets", {})
        return {str(k): str(v) for k, v in items.items() if str(k).strip()}
    except Exception as exc:
        log.warning("Could not read %s for editor: %s", src, exc)
        return {}


def save_snippets(mapping: dict) -> None:
    """Replace ONLY the snippets in snippets.json, preserving all other
    top-level keys (_comment, etc.). Writes atomically (temp file in the same
    directory, then os.replace) and refreshes the module cache so the change is
    live on the next dictation without an mtime race."""
    # Start from the real snippets.json if it exists, else the example, so a
    # first-time save still seeds any comment keys.
    src = _source()
    try:
        existing = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}
        if not isinstance(existing, dict):
            existing = {}
    except Exception as exc:
        log.warning("Could not read %s before save, starting fresh: %s", src, exc)
        existing = {}

    clean = {str(k): str(v) for k, v in mapping.items() if str(k).strip()}
    existing["snippets"] = clean

    tmp = SNIP_FILE.with_name(SNIP_FILE.name + ".tmp")
    text = json.dumps(existing, indent=2, ensure_ascii=False)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, SNIP_FILE)

    # Refresh the cache from what we just wrote, normalising keys the same way
    # _load does so matching stays consistent (no mtime read race).
    _cache["snippets"] = {
        _normalise(str(k)): str(v)
        for k, v in clean.items()
        if str(k).strip()
    }
    try:
        _cache["mtime"] = SNIP_FILE.stat().st_mtime
    except Exception:
        _cache["mtime"] = -1.0
    log.info("Saved %d snippets to %s", len(clean), SNIP_FILE.name)
    backup_if_changed(SNIP_FILE)
