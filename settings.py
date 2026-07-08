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
import os
from dataclasses import dataclass, field, fields
from typing import Any

from paths import user_file

_SETTINGS_FILE = user_file("settings.json")

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
    # Rules 7-16: sensible defaults for common professional apps. Appended
    # after the rules above so existing behaviour keeps precedence; override
    # any of these in settings.json's own router_rules list if needed.
    # Rule 7: Outlook desktop -> polished
    {
        "match": "process",
        "pattern": "outlook.exe",
        "mode": "polished",
    },
    # Rule 8: Microsoft Word -> polished
    {
        "match": "process",
        "pattern": "winword.exe",
        "mode": "polished",
    },
    # Rule 9: Thunderbird -> polished
    {
        "match": "process",
        "pattern": "thunderbird.exe",
        "mode": "polished",
    },
    # Rule 10: Slack -> note
    {
        "match": "process",
        "pattern": "slack.exe",
        "mode": "note",
    },
    # Rule 11: Microsoft Teams (classic + new) -> note
    {
        "match": "process_regex",
        "pattern": r"^(teams|ms-teams)\.exe$",
        "mode": "note",
    },
    # Rule 12: Discord -> note
    {
        "match": "process",
        "pattern": "discord.exe",
        "mode": "note",
    },
    # Rule 13: WhatsApp desktop -> note
    {
        "match": "process",
        "pattern": "whatsapp.exe",
        "mode": "note",
    },
    # Rule 14: Notion -> brand_voice
    {
        "match": "process",
        "pattern": "notion.exe",
        "mode": "brand_voice",
    },
    # Rule 15: Gmail or Outlook web in any browser title -> polished
    {
        "match": "title",
        "pattern": "gmail",
        "mode": "polished",
    },
    {
        "match": "title",
        "pattern": "outlook",
        "mode": "polished",
    },
    # Rule 16: X/Twitter in any browser title -> note
    {
        "match": "title",
        "pattern": "x.com",
        "mode": "note",
    },
    {
        "match": "title",
        "pattern": "twitter",
        "mode": "note",
    },
]


_DEFAULT_VOICE_COMMANDS: list[dict] = [
    {"phrases": ["scratch that",
                 "delete that"],           "action": "key",  "value": "ctrl+z"},
    {"phrases": ["send it",
                 "send message",
                 "send"],                  "action": "key",  "value": "enter"},
    {"phrases": ["undo paste",
                 "scratch paste"],         "action": "undo_paste", "value": ""},
]

# Capture commands: utterances that START WITH one of these phrases are routed
# to the content-capture script instead of being pasted.  The trigger phrase
# (plus any immediately following colon, comma or whitespace) is stripped to
# yield the payload text sent to the script.
_DEFAULT_CAPTURE_COMMANDS: list[str] = [
    "content idea",
    "content note",
]

# Absolute path to the Persian CLAW content-capture script.
_DEFAULT_CONTENT_CAPTURE_SCRIPT: str = (
    r"D:\01 Projects\Persian CLAW\scripts\content_capture.py"
)

# Edit commands: utterances that START WITH one of these phrases trigger
# natural-language editing of the current text selection (e.g. "edit this:
# make it more formal"). Same trigger-phrase + payload-strip convention as
# capture commands.
_DEFAULT_EDIT_COMMANDS: list[str] = [
    "edit this",
    "rewrite this",
    "edit selection",
]

# Inline formatting commands: recognised anywhere in an utterance (case-insensitive,
# word-boundary match).  Each entry maps one or more spoken phrases to a number of
# newlines that should be inserted at that position.
_DEFAULT_INLINE_FORMATTING: list[dict] = [
    {"phrases": ["new paragraph"],         "newlines": 2},
    {"phrases": ["new line", "next line"], "newlines": 1},
]

# Translate mode meta-text/instruction-echo detector (quality_guard.py).
# A translation that contains one of these phrases is almost certainly the
# model narrating its own reasoning instead of translating, and is rejected
# regardless of guard_level. Case-insensitive substring match. Extend via
# settings.json without touching source code.
_DEFAULT_TRANSLATE_META_PATTERNS: list[str] = [
    "i should say in english",
    "as an ai",
    "here is the translation",
    "the request in persian",
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
    # Language handling for Whisper transcription.
    # "en" (default) locks English so accented English speech is never
    # hallucinated into another script (Persian/Turkish auto-detect
    # false positives seen in production). Set to "auto" to detect the
    # language per utterance from the audio, or a forced ISO code
    # ("fa", ...) to pass that language to Whisper as the `language`
    # parameter regardless of accent.
    dictation_language: str = "en"
    # When True, the dictionary "terms" glossary is sent to Whisper as a bias
    # prompt to improve name spelling. OFF by default: a glossary of non-English
    # proper nouns can flip Whisper's language auto-detection (accented English
    # transcribed as Persian). Substitutions still fix mis-hearings afterwards.
    whisper_glossary_bias: bool = False

    # transcribe_local.py — optional offline fallback via faster-whisper. Only
    # engaged when the Groq HTTP call fails with a network-class error
    # (connection error, timeout, DNS) AND the optional faster-whisper package
    # is installed (see requirements-optional.txt). Strictly optional: without
    # the package installed, behaviour is unchanged regardless of this flag.
    offline_fallback_enabled: bool = True
    local_whisper_model: str = "small"

    # cleanup.py
    cleanup_model: str = "llama-3.3-70b-versatile"
    cleanup_timeout_s: float = 2.0
    cleanup_timeout_translate_s: float = 3.5
    # Additional seconds granted per 100 characters of transcript so long
    # utterances do not hit the flat timeout and fall back to raw Whisper output.
    cleanup_timeout_per_100_chars_s: float = 0.3

    # dictionary.py
    max_prompt_chars: int = 220

    # meeting.py: meeting notes mode, fixed-length mic chunk duration.
    meeting_chunk_seconds: int = 60

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

    # Capture commands: list of trigger phrases (normalised, case-insensitive)
    # whose utterances are routed to content_capture_script rather than pasted.
    capture_commands: tuple = field(
        default_factory=lambda: tuple(_DEFAULT_CAPTURE_COMMANDS)
    )

    # Absolute path to the content-capture script called by capture commands.
    content_capture_script: str = _DEFAULT_CONTENT_CAPTURE_SCRIPT

    # Edit commands: list of trigger phrases (normalised, case-insensitive)
    # whose utterances trigger natural-language editing of the current text
    # selection via cleanup.edit_text.
    edit_commands: tuple = field(
        default_factory=lambda: tuple(_DEFAULT_EDIT_COMMANDS)
    )

    # brand_voice mode: brand identity injected into prompts/brand_voice.txt.
    # brand_name replaces {{brand_name}} in the prompt template.
    # brand_voice_notes is appended as extra brand guidance (optional, can be empty).
    # When unset, brand_name defaults to "the user's brand" so the mode is portable.
    brand_name: str = "the user's brand"
    brand_voice_notes: str = ""

    # Input backend: "keyboard" (default, requires admin rights, uses the
    # keyboard lib) or "pynput" (no admin required, uses pynput.keyboard;
    # see requirements-optional.txt).
    # DEFAULT MUST remain "keyboard" to preserve the existing hold-to-talk
    # behaviour byte-for-byte for all existing users.
    input_backend: str = "keyboard"

    # quality_guard.py
    # "fast"  -> word-count ratio + edit-distance only (default, no extra deps)
    # "full"  -> also runs cosine similarity via sentence-transformers (optional dep)
    quality_guard_level: str = "fast"

    # Extra Whisper noise-token patterns beyond the hardcoded set in transcribe.py.
    # Users can append known phantom tokens here via settings.json so they do not
    # need to edit source code.
    extra_noise_patterns: tuple = field(default_factory=tuple)

    # quality_guard.py translate-mode meta-text/instruction-echo detector.
    # Case-insensitive substring patterns; a match rejects the translation.
    translate_meta_patterns: tuple = field(
        default_factory=lambda: tuple(_DEFAULT_TRANSLATE_META_PATTERNS)
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
# Field annotations are stored as plain strings by `from __future__ import
# annotations`, so DictationSettings' scalar fields carry their type name
# ("int", "float", "str", "bool") rather than the type object. That is all
# this needs: container fields (router_rules, voice_commands, ...) keep their
# own isinstance handling below and are skipped here.

_SCALAR_TYPE_NAMES = {"int", "float", "str", "bool"}


def _scalar_type_ok(value: Any, expected: str) -> bool:
    if expected == "bool":
        return isinstance(value, bool)
    if expected == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "str":
        return isinstance(value, str)
    return True


def _validate_raw_settings(raw: dict[str, Any]) -> set[str]:
    """Warn (never raise) on unknown top-level keys or scalar type mismatches.

    Keys starting with "_" are treated as inline JSON comments (settings.json.example
    uses "_comment*" keys for documentation) and are never flagged. Returns the
    set of key names whose value failed the type check, so the caller can skip
    them and fall back to the dataclass default.
    """
    field_types = {f.name: f.type for f in fields(DictationSettings)}
    invalid: set[str] = set()
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if key not in field_types:
            log.warning("settings.json: unknown key %r ignored (check for a typo).", key)
            continue
        expected = field_types[key]
        if expected not in _SCALAR_TYPE_NAMES:
            continue
        if not _scalar_type_ok(value, expected):
            log.warning(
                "settings.json: %r expected %s, got %s; ignoring override.",
                key, expected, type(value).__name__,
            )
            invalid.add(key)
    return invalid


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

    invalid_keys = _validate_raw_settings(raw)

    # Pull known scalar keys. Unknown keys and type-mismatched values were
    # warned about in _validate_raw_settings; mismatches are skipped here so
    # the dataclass default applies instead of a wrong runtime type.
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
        "cleanup_timeout_per_100_chars_s",
        "max_prompt_chars",
        "local_whisper_model",
        "meeting_chunk_seconds",
    ]
    for key in scalar_keys:
        if key in raw and key not in invalid_keys:
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

    if "capture_commands" in raw:
        cmds = raw["capture_commands"]
        if isinstance(cmds, list):
            kwargs["capture_commands"] = tuple(str(c) for c in cmds)

    if "content_capture_script" in raw:
        kwargs["content_capture_script"] = str(raw["content_capture_script"])

    if "edit_commands" in raw:
        cmds = raw["edit_commands"]
        if isinstance(cmds, list):
            kwargs["edit_commands"] = tuple(str(c) for c in cmds)

    if "quality_guard_level" in raw:
        kwargs["quality_guard_level"] = str(raw["quality_guard_level"])

    if "dictation_language" in raw:
        kwargs["dictation_language"] = str(raw["dictation_language"])

    if "whisper_glossary_bias" in raw:
        kwargs["whisper_glossary_bias"] = bool(raw["whisper_glossary_bias"])

    if "offline_fallback_enabled" in raw:
        kwargs["offline_fallback_enabled"] = bool(raw["offline_fallback_enabled"])

    for key in ("brand_name", "brand_voice_notes", "input_backend"):
        if key in raw:
            kwargs[key] = str(raw[key])

    if "extra_noise_patterns" in raw:
        patterns = raw["extra_noise_patterns"]
        if isinstance(patterns, list):
            kwargs["extra_noise_patterns"] = tuple(str(p) for p in patterns)

    if "translate_meta_patterns" in raw:
        patterns = raw["translate_meta_patterns"]
        if isinstance(patterns, list):
            kwargs["translate_meta_patterns"] = tuple(str(p) for p in patterns)

    return DictationSettings(**kwargs)


# Module-level singleton. Imported directly by consumer modules.
settings = _load_settings()


# ---------------------------------------------------------------------------
# Runtime persistence helpers (tray + overlay language toggle)
# ---------------------------------------------------------------------------

def save_setting(key: str, value: Any) -> None:
    """Persist a single setting to settings.json, preserving all other keys.

    Reads the existing file (if present) so unknown or unrelated keys survive
    untouched, updates only *key*, and writes atomically (temp file then
    os.replace) so a crash mid-write cannot corrupt the file. Never rewrites
    the full set of defaults, only the changed key.

    Calls `user_file` fresh each time (rather than the cached `_SETTINGS_FILE`
    module constant) so tests can monkeypatch it without reimporting this
    module.
    """
    target = user_file("settings.json")
    raw: dict[str, Any] = {}
    if target.exists():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(
                "save_setting: failed to read existing settings.json (%s); starting fresh.",
                exc,
            )
            raw = {}
    raw[key] = value
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def set_dictation_language(lang: str) -> None:
    """Update the live settings singleton and persist dictation_language.

    DictationSettings is a frozen dataclass, so a plain attribute assignment
    would raise. object.__setattr__ is the standard escape hatch for this
    single, controlled mutation. The write is a single attribute assignment,
    atomic under the GIL, so worker threads reading settings.dictation_language
    concurrently never see a torn value.
    """
    object.__setattr__(settings, "dictation_language", lang)
    save_setting("dictation_language", lang)
    log.info("dictation_language set to %r", lang)
