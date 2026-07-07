"""test_router.py — regression tests for router.pick_mode() routing rules.

Each test monkeypatches router.settings to a fresh DictationSettings()
instance so the default router_rules apply regardless of any local
settings.json override on the machine running the suite.
"""

from __future__ import annotations

import router
from settings import DictationSettings


def _use_default_rules(monkeypatch):
    monkeypatch.setattr(router, "settings", DictationSettings())


def test_vscode_routes_raw(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("Code.exe", "some file.py - Visual Studio Code") == "raw"


def test_jetbrains_regex_routes_raw(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("idea64.exe", "MyProject - IntelliJ IDEA") == "raw"


def test_terminal_with_claude_title_routes_prompt(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("powershell.exe", "claude - my session") == "prompt"


def test_telegram_routes_note(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("Telegram.exe", "Telegram") == "note"


def test_obsidian_routes_brand_voice(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("Obsidian.exe", "My Vault - Obsidian") == "brand_voice"


def test_linkedin_title_routes_brand_voice(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("chrome.exe", "LinkedIn - Feed") == "brand_voice"


def test_unmatched_defaults_to_polished(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("notepad.exe", "untitled - Notepad") == "polished"
