"""
_smoke_substitutions.py -- offline smoke test for dictionary substitutions
and snippet expansion in the dispatch chain.

Runs without any network calls, audio hardware, or GUI. Uses only the
example JSON files so no personal dictionary.json is required.

Run from the freeflow-windows root:
    python _smoke_substitutions.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# Ensure the repo root is on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DICTATION_DIR = Path(__file__).resolve().parent


def _write_temp_json(data: dict) -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(data, f)
    f.close()
    return Path(f.name)


def _assert(condition: bool, msg: str) -> None:
    if not condition:
        print(f"  FAIL: {msg}")
        sys.exit(1)
    print(f"  pass: {msg}")


# ---------------------------------------------------------------------------
# Test 1: apply_substitutions directly
# ---------------------------------------------------------------------------

def test_apply_substitutions() -> None:
    print("1. apply_substitutions()")
    from dictionary import apply_substitutions, _cache, _source

    # Pin the cache mtime to the real source file's mtime so _load() takes
    # the cache-hit branch and never re-reads the file during the test.
    real_mtime = _source().stat().st_mtime if _source().exists() else -1.0
    original_mtime = _cache["mtime"]
    original_data = _cache["data"]

    _cache["mtime"] = real_mtime
    _cache["data"] = {
        "terms": [],
        "substitutions": {
            "see lux": "Silux",
            "silex": "Silux",
            "hammered arab": "Hamed Arab",
            "persian craw": "Persian CLAW",
        },
    }

    try:
        _assert(apply_substitutions("see lux London") == "Silux London",
                "see lux -> Silux")
        _assert(apply_substitutions("silex ring") == "Silux ring",
                "silex -> Silux")
        _assert(apply_substitutions("my name is hammered arab") == "my name is Hamed Arab",
                "hammered arab -> Hamed Arab")
        _assert(apply_substitutions("persian craw is great") == "Persian CLAW is great",
                "persian craw -> Persian CLAW")
        _assert(apply_substitutions("no match here") == "no match here",
                "passthrough when no match")
        _assert(apply_substitutions("") == "",
                "empty string passthrough")
    finally:
        _cache["mtime"] = original_mtime
        _cache["data"] = original_data


# ---------------------------------------------------------------------------
# Test 2: expand_snippet
# ---------------------------------------------------------------------------

def test_expand_snippet() -> None:
    print("2. expand_snippet()")
    from snippets import expand_snippet, _cache, _source

    real_mtime = _source().stat().st_mtime if _source().exists() else -1.0
    original_mtime = _cache["mtime"]
    original_snippets = _cache["snippets"]

    _cache["mtime"] = real_mtime
    _cache["snippets"] = {
        "calendar link": "https://luma.com/test",
        "email signoff": "Best,\nHamed",
    }

    try:
        _assert(expand_snippet("calendar link") == "https://luma.com/test",
                "exact cue match")
        _assert(expand_snippet("Calendar Link") == "https://luma.com/test",
                "case-insensitive match")
        _assert(expand_snippet("calendar link.") == "https://luma.com/test",
                "trailing punctuation stripped")
        _assert(expand_snippet("email signoff") == "Best,\nHamed",
                "multi-line expansion")
        _assert(expand_snippet("no cue") is None,
                "no match returns None")
        _assert(expand_snippet("") is None,
                "empty string returns None")
    finally:
        _cache["mtime"] = original_mtime
        _cache["snippets"] = original_snippets


# ---------------------------------------------------------------------------
# Test 3: substitutions run BEFORE snippet matching
# (simulates the transcribe() -> clean() dispatch chain)
# ---------------------------------------------------------------------------

def test_substitution_before_snippet() -> None:
    print("3. substitution before snippet (dispatch chain)")
    from dictionary import apply_substitutions, _cache as dict_cache
    from snippets import expand_snippet, _cache as snip_cache

    from dictionary import _source as dict_source
    from snippets import _source as snip_source

    dict_real_mtime = dict_source().stat().st_mtime if dict_source().exists() else -1.0
    snip_real_mtime = snip_source().stat().st_mtime if snip_source().exists() else -1.0

    orig_dict_mtime = dict_cache["mtime"]
    orig_dict_data = dict_cache["data"]
    orig_snip_mtime = snip_cache["mtime"]
    orig_snip_snippets = snip_cache["snippets"]

    # Dictionary maps "calender link" (mis-heard) -> "calendar link"
    # Snippet maps "calendar link" -> expansion
    dict_cache["mtime"] = dict_real_mtime
    dict_cache["data"] = {
        "terms": [],
        "substitutions": {"calender link": "calendar link"},
    }
    snip_cache["mtime"] = snip_real_mtime
    snip_cache["snippets"] = {"calendar link": "https://luma.com/test"}

    try:
        # This is what transcribe.py does: apply substitutions after Whisper.
        mis_heard = "calender link"
        after_subs = apply_substitutions(mis_heard)
        _assert(after_subs == "calendar link",
                "mis-heard 'calender link' corrected by substitution")

        # This is what cleanup.py (clean()) does next: try snippet.
        expansion: Optional[str] = expand_snippet(after_subs)
        _assert(expansion == "https://luma.com/test",
                "snippet matched on corrected text")
    finally:
        dict_cache["mtime"] = orig_dict_mtime
        dict_cache["data"] = orig_dict_data
        snip_cache["mtime"] = orig_snip_mtime
        snip_cache["snippets"] = orig_snip_snippets


# ---------------------------------------------------------------------------
# Test 4: end-to-end via transcribe() -- patches the HTTP call
# ---------------------------------------------------------------------------

def test_transcribe_applies_substitutions() -> None:
    print("4. transcribe() applies substitutions (HTTP mocked)")

    from dictionary import _cache as dict_cache

    orig_dict = dict(dict_cache)
    dict_cache["mtime"] = 9999999.0
    dict_cache["data"] = {
        "terms": [],
        "substitutions": {"see lux": "Silux"},
    }

    fake_response = MagicMock()
    fake_response.json.return_value = {"text": "see lux London", "language": "en"}
    fake_response.raise_for_status = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = Path(tmp.name)
        tmp.write(b"\x00" * 44)  # minimal placeholder

    try:
        with patch("transcribe.requests.post",
                   return_value=fake_response):
            from transcribe import transcribe
            text, lang = transcribe(wav, api_key="fake-key")

        _assert(text == "Silux London",
                "transcribe() applies substitution before returning")
        _assert(lang == "en", "language returned correctly")
    finally:
        dict_cache.update(orig_dict)
        wav.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Run all
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 5: hallucination filter
# ---------------------------------------------------------------------------

def test_hallucination_filter() -> None:
    print("5. _is_hallucination()")
    from transcribe import _is_hallucination

    positives = [
        "Siyasat Suleiman Sleuthier Sleuthier",  # Hamed reported 2026-05-23
        "Thank you Thank you Thank you",
        "the the the cat",
        "Subscribe Subscribe",
        "one two one two one two",
        "go go go",
    ]
    for text in positives:
        _assert(_is_hallucination(text) is True, f"flagged: {text!r}")

    negatives = [
        "",
        "hello",
        "hello world how are you today",
        "the quick brown fox",
        "the quick brown the fox",
        "I love love it",
        "this is a normal sentence",
        "the the",
    ]
    for text in negatives:
        _assert(_is_hallucination(text) is False, f"passthrough: {text!r}")


# ---------------------------------------------------------------------------
# Test 6: tail-trim heuristic
# ---------------------------------------------------------------------------

def test_trim_hallucination_tail() -> None:
    print("6. _trim_hallucination_tail()")
    from transcribe import _trim_hallucination_tail

    # The screenshot case Hamed reported 2026-05-23.
    src = (
        "I need an assistant that makes my life easier, instead of just "
        "thinking. Aneera Mishra, Sibaraj, Sajid, Sadegh, Sadegh."
    )
    out = _trim_hallucination_tail(src)
    _assert(
        out == "I need an assistant that makes my life easier, instead of just thinking.",
        "trims comma-list with repeated tail name",
    )

    # No repeat in the tail = no change.
    src2 = "I need an assistant that makes my life easier."
    _assert(_trim_hallucination_tail(src2) == src2, "untouched when no tail repeat")

    # Repeat exists but would trim away >50% of the text = bail out.
    src3 = "Hello Sadegh Sadegh"
    _assert(_trim_hallucination_tail(src3) == src3, "bail when trim would be too aggressive")

    # Short transcript = bail out.
    _assert(_trim_hallucination_tail("Yes please") == "Yes please", "short transcript bail")


# ---------------------------------------------------------------------------
# Test 7: per-segment filter
# ---------------------------------------------------------------------------

def test_filter_segments() -> None:
    print("7. _filter_segments()")
    from transcribe import _filter_segments

    payload = {
        "text": "good prefix garbage tail",
        "segments": [
            {"text": "good prefix", "no_speech_prob": 0.05,
             "compression_ratio": 1.1, "avg_logprob": -0.2},
            {"text": " garbage tail",  "no_speech_prob": 0.9,
             "compression_ratio": 3.5, "avg_logprob": -2.1},
        ],
    }
    out = _filter_segments(payload)
    _assert(out == "good prefix", "drops segment with high no_speech_prob")

    payload2 = {
        "text": "all good",
        "segments": [
            {"text": "all good", "no_speech_prob": 0.1,
             "compression_ratio": 1.0, "avg_logprob": -0.3},
        ],
    }
    _assert(_filter_segments(payload2) == "all good", "keeps clean segment")

    # No segments -> fall back to payload['text'].
    payload3 = {"text": "fallback text"}
    _assert(_filter_segments(payload3) == "fallback text", "falls back when no segments")


if __name__ == "__main__":
    test_apply_substitutions()
    test_expand_snippet()
    test_substitution_before_snippet()
    test_transcribe_applies_substitutions()
    test_hallucination_filter()
    test_trim_hallucination_tail()
    test_filter_segments()
    print("\nAll smoke tests passed.")
