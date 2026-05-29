"""
settings.py — dictation service configuration, single source of truth.

Loads settings.json (if present) over a complete set of defaults baked in
here. A missing or partial settings.json reproduces the original hardcoded
behaviour exactly.

Usage:
    from settings import settings
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SETTINGS_FILE = _HERE / "settings.json"

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router rule schema
# ---------------------------------------------------------------------------
# Each entry is {"match": "process"|"title", "pattern": str, "mode": str}.
# "process" rules are compared against the lowercased process name.
# "title" rules use a substring match against the lowercased window title.
# An optional "process_also" key on title rules additionally requires the
# process name to be in the given list (used for the terminal+claude rule).
#
# Exact current routing order and behaviour is reproduced by the defaults
# below.  The router iterates this list; first match wins; no match -> polished.

_DEFAULT_ROUTER_RULES: list[dict[str, Any]] = [
    # Rule 1: VS Code -> raw
    {
        "match": "process",
        "pattern": "code.exe",
        "mode": "raw",
    },
    # Rule 2: JetBrains IDEs -> raw (regex on process name)
    {
        "match": "process_regex",
        "pattern": r"^(idea64|pycharm64|webstorm64|goland64|clion64|rider64|datagrip64|fleet|phpstorm64)\.exe$",
        "mode": "raw",
    },
    # Rule 3: AI terminals (claude/claw in title) -> prompt
    {
        "match": "title",
        "pattern": "claude",
        "process_also": [
            "windowsterminal.exe",
            "pwsh.exe",
            "powershell.exe",
            "cmd.exe",
            "wezterm.exe",
            "alacritty.exe",
        ],
        "mode": "prompt",
    },
    {
        "match": "title",
        "pattern": "claw",
        "process_also": [
            "windowsterminal.exe",
            "pwsh.exe",
            "powershell.exe",
            "cmd.exe",
            "wezterm.exe",
            "alacritty.exe",
        ],
        "mode": "prompt",
    },
    # Rule 4: Telegram -> note
    {
        "match": "process",
        "pattern": "telegram.exe",
        "mode": "note",
    },
    # Rule 5: Obsidian -> brand_voice
    {
        "match": "process",
        "pattern": "obsidian.exe",
        "mode": "brand_voice",
    },
    # Rule 6: LinkedIn in any browser title -> brand_voice
    {
        "match": "title",
        "pattern": "linkedin",
        "mode": "brand_voice",
    },
]


_DEFAULT_VOICE_COMMANDS: list[dict] = [
    {"phrases": ["scratch that",
                 "delete that"],           "action": "key",  "value": "ctrl+z"},
    {"phrases": ["send it",
                 "send message",
                 "send"],                  "action": "key",  "value": "enter"},
]

# Inline formatting commands: recognised anywhere in an utterance (case-insensitive,
# word-boundary match).  Each entry maps one or more spoken phrases to a number of
# newlines that should be inserted at that position.
_DEFAULT_INLINE_FORMATTING: list[dict] = [
    {"phrases": ["new paragraph"],         "newlines": 2},
    {"phrases": ["new line", "next line"], "newlines": 1},
]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DictationSettings:
    # main.py
    hotkey: str = "1"
    hotkey_modifier: str = "alt"
    double_tap_window_ms: int = 400
    short_tap_max_ms: int = 250

    # vad.py
    vad_aggressiveness: int = 2
    vad_frame_duration_ms: int = 20
    vad_speech_frames_to_start: int = 15
    vad_silence_frames_to_end: int = 75
    vad_pre_roll_frames: int = 25
    vad_min_burst_frames: int = 25
    vad_max_burst_frames: int = 1500

    # transcribe.py
    whisper_model: str = "whisper-large-v3"
    transcribe_timeout_s: int = 60
    no_speech_prob_ceiling: float = 0.7
    seg_no_speech: float = 0.6
    seg_compression: float = 2.0
    seg_logprob: float = -2.0

    # cleanup.py
    cleanup_model: str = "llama-3.3-70b-versatile"
    cleanup_timeout_s: float = 2.0
    cleanup_timeout_translate_s: float = 3.5

    # dictionary.py
    max_prompt_chars: int = 220

    # router.py — list preserved as a tuple of frozen mappings at runtime
    router_rules: tuple = field(
        default_factory=lambda: tuple(
            dict(r) for r in _DEFAULT_ROUTER_RULES
        )
    )

    # voice_commands — list of {phrases: [...], action: "text"|"key", value: str}
    # Checked before snippets; whole-transcript match only.
    voice_commands: tuple = field(
        default_factory=lambda: tuple(_DEFAULT_VOICE_COMMANDS)
    )

    # inline_formatting — list of {phrases: [...], newlines: int}
    # Matched case-insensitively on word boundaries anywhere in the utterance.
    # "new paragraph" -> 2 newlines, "new line"/"next line" -> 1 newline.
    inline_formatting: tuple = field(
        default_factory=lambda: tuple(_DEFAULT_INLINE_FORMATTING)
    )

    # cleanup.py code-switching preservation
    codeswitching_preserve: bool = True
    codeswitching_prompt: str = (
        "The speaker may mix Persian (Farsi) and English within a single utterance. "
        "Preserve both languages exactly as spoken: do not translate, transliterate, "
        "or collapse mixed Farsi-English into a single language. "
        "Keep Persian script for Farsi words and English script for English words."
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_settings() -> DictationSettings:
    if not _SETTINGS_FILE.exists():
        return DictationSettings()

    try:
        raw: dict[str, Any] = json.loads(
            _SETTINGS_FILE.read_text(encoding="utf-8")
        )
    except Exception as exc:
        log.warning("Failed to parse settings.json (%s); using defaults.", exc)
        return DictationSettings()

    # Pull known scalar keys; ignore unknown keys silently.
    kwargs: dict[str, Any] = {}
    scalar_keys = [
        "hotkey",
        "hotkey_modifier",
        "double_tap_window_ms",
        "short_tap_max_ms",
        "vad_aggressiveness",
        "vad_frame_duration_ms",
        "vad_speech_frames_to_start",
        "vad_silence_frames_to_end",
        "vad_pre_roll_frames",
        "vad_min_burst_frames",
        "vad_max_burst_frames",
        "whisper_model",
        "transcribe_timeout_s",
        "no_speech_prob_ceiling",
        "seg_no_speech",
        "seg_compression",
        "seg_logprob",
        "cleanup_model",
        "cleanup_timeout_s",
        "cleanup_timeout_translate_s",
        "max_prompt_chars",
    ]
    for key in scalar_keys:
        if key in raw:
            kwargs[key] = raw[key]

    if "router_rules" in raw:
        rules = raw["router_rules"]
        if isinstance(rules, list):
            kwargs["router_rules"] = tuple(dict(r) for r in rules)

    if "voice_commands" in raw:
        cmds = raw["voice_commands"]
        if isinstance(cmds, list):
            kwargs["voice_commands"] = tuple(dict(c) for c in cmds)

    if "inline_formatting" in raw:
        fmts = raw["inline_formatting"]
        if isinstance(fmts, list):
            kwargs["inline_formatting"] = tuple(dict(f) for f in fmts)

    for key in ("codeswitching_preserve", "codeswitching_prompt"):
        if key in raw:
            kwargs[key] = raw[key]

    return DictationSettings(**kwargs)


# Module-level singleton. Imported directly by consumer modules.
settings = _load_settings()
