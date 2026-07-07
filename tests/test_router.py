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


def test_outlook_routes_polished(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("outlook.exe", "Inbox - Outlook") == "polished"


def test_slack_routes_note(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("slack.exe", "Slack | #general") == "note"


def test_teams_classic_routes_note(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("teams.exe", "Microsoft Teams") == "note"


def test_teams_new_routes_note(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("ms-teams.exe", "Microsoft Teams") == "note"


def test_notion_routes_brand_voice(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("notion.exe", "My Page - Notion") == "brand_voice"


def test_gmail_title_routes_polished(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("chrome.exe", "Inbox (3) - Gmail") == "polished"


def test_twitter_title_routes_note(monkeypatch):
    _use_default_rules(monkeypatch)
    assert router.pick_mode("chrome.exe", "Home / Twitter") == "note"


def test_vscode_still_routes_raw_despite_later_rules(monkeypatch):
    """VS Code -> raw is rule 1; the new rules 7-16 are appended after it, so
    precedence (first match in settings.router_rules wins) must be unchanged."""
    _use_default_rules(monkeypatch)
    assert router.pick_mode("Code.exe", "some file.py - Visual Studio Code") == "raw"
