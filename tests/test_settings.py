"""test_settings.py — regression tests for settings.py.

Covers fresh defaults, the partial settings.json overlay, save_setting's
preserve-unknown-keys behaviour, and set_dictation_language. All file
access is redirected to tmp_path via the isolated_settings_file fixture
(conftest.py); the real %APPDATA%\\FreeFlow\\settings.json is never read
or written.
"""

from __future__ import annotations

import json
import logging

import settings as settings_module
from settings import DictationSettings


def test_default_dictation_language_is_en():
    assert DictationSettings().dictation_language == "en"


def test_load_settings_missing_file_returns_defaults(isolated_settings_file):
    # The fixture points at a tmp_path file that has not been written yet.
    loaded = settings_module._load_settings()
    assert loaded == DictationSettings()


def test_partial_settings_json_merges_with_defaults(isolated_settings_file):
    isolated_settings_file.write_text(
        json.dumps({"dictation_language": "fa"}), encoding="utf-8"
    )
    loaded = settings_module._load_settings()
    assert loaded.dictation_language == "fa"
    # Every other field falls back to its default, untouched by the overlay.
    assert loaded.hotkey == "1"
    assert loaded.whisper_model == "whisper-large-v3"


def test_save_setting_writes_valid_json_and_preserves_unknown_key(isolated_settings_file):
    isolated_settings_file.write_text(json.dumps({"custom_key": 1}), encoding="utf-8")
    settings_module.save_setting("dictation_language", "fa")
    raw = json.loads(isolated_settings_file.read_text(encoding="utf-8"))
    assert raw["custom_key"] == 1
    assert raw["dictation_language"] == "fa"


def test_save_setting_updates_only_target_key(isolated_settings_file):
    isolated_settings_file.write_text(
        json.dumps({"dictation_language": "en", "hotkey": "2"}), encoding="utf-8"
    )
    settings_module.save_setting("hotkey", "3")
    raw = json.loads(isolated_settings_file.read_text(encoding="utf-8"))
    assert raw["hotkey"] == "3"
    assert raw["dictation_language"] == "en"


def test_unknown_key_loads_defaults_and_warns(isolated_settings_file, caplog):
    isolated_settings_file.write_text(json.dumps({"nonexistent_key": 1}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        loaded = settings_module._load_settings()
    assert loaded == DictationSettings()
    assert any("nonexistent_key" in r.message for r in caplog.records)


def test_scalar_type_mismatch_ignored_default_survives(isolated_settings_file, caplog):
    isolated_settings_file.write_text(
        json.dumps({"transcribe_timeout_s": "sixty"}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        loaded = settings_module._load_settings()
    assert loaded.transcribe_timeout_s == DictationSettings().transcribe_timeout_s
    assert any("transcribe_timeout_s" in r.message for r in caplog.records)


def test_comment_key_never_flagged(isolated_settings_file, caplog):
    isolated_settings_file.write_text(
        json.dumps({"_comment": "some documentation", "dictation_language": "fa"}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        loaded = settings_module._load_settings()
    assert loaded.dictation_language == "fa"
    assert not any("_comment" in r.message for r in caplog.records)


def test_set_dictation_language_updates_live_singleton_and_file(isolated_settings_file):
    original = settings_module.settings.dictation_language
    try:
        settings_module.set_dictation_language("fa")
        assert settings_module.settings.dictation_language == "fa"
        raw = json.loads(isolated_settings_file.read_text(encoding="utf-8"))
        assert raw["dictation_language"] == "fa"
    finally:
        # DictationSettings is frozen; restore the module singleton so this
        # test never leaks state into others in the same process.
        object.__setattr__(settings_module.settings, "dictation_language", original)
