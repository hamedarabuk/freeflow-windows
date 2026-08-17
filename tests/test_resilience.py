"""
Tests for the model-retirement resilience (cleanup._is_model_rejected) and
the tray-driven router-rule capture (settings.add_router_rule).
"""

from __future__ import annotations

import json

import requests

import cleanup
import settings as settings_module


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _http_error(status: int, body: dict | None = None) -> requests.HTTPError:
    exc = requests.HTTPError("boom")
    exc.response = _Resp(status, body)  # type: ignore[assignment]
    return exc


# --------------------------------------------------------------------------
# _is_model_rejected: model-id failures versus everything else
# --------------------------------------------------------------------------

def test_404_is_model_rejected():
    # Groq answers 404 model_not_found for unknown ids (observed live when
    # llama-3.3-70b-versatile was retired on 16 Aug 2026).
    assert cleanup._is_model_rejected(_http_error(404)) is True


def test_400_decommissioned_is_model_rejected():
    err = _http_error(
        400, {"error": {"code": "model_decommissioned", "message": "gone"}}
    )
    assert cleanup._is_model_rejected(err) is True


def test_500_is_not_model_rejected():
    # Transient server errors must NOT flip the session to the fallback model.
    assert cleanup._is_model_rejected(_http_error(500)) is False


def test_network_error_is_not_model_rejected():
    assert cleanup._is_model_rejected(requests.ConnectionError()) is False


def test_unrelated_400_is_not_model_rejected():
    err = _http_error(400, {"error": {"code": "bad_request", "message": "temperature out of range"}})
    assert cleanup._is_model_rejected(err) is False


# --------------------------------------------------------------------------
# add_router_rule: prepend, replace, persist
# --------------------------------------------------------------------------

def test_add_router_rule_prepends_replaces_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "user_file", lambda name: tmp_path / name)
    original = settings_module.settings.router_rules
    try:
        settings_module.add_router_rule("Foo.EXE", "note")
        rules = settings_module.settings.router_rules
        # Prepended (first-match-wins router) and normalised to lower case.
        assert rules[0] == {"match": "process", "pattern": "foo.exe", "mode": "note"}
        assert len(rules) == len(original) + 1

        # Re-routing the same app replaces, never duplicates.
        settings_module.add_router_rule("foo.exe", "raw")
        rules2 = settings_module.settings.router_rules
        assert rules2[0]["mode"] == "raw"
        assert len(rules2) == len(rules)

        # Persisted to settings.json.
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert data["router_rules"][0] == {"match": "process", "pattern": "foo.exe", "mode": "raw"}
    finally:
        object.__setattr__(settings_module.settings, "router_rules", original)


def test_add_router_rule_ignores_empty_process(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "user_file", lambda name: tmp_path / name)
    original = settings_module.settings.router_rules
    try:
        settings_module.add_router_rule("   ", "note")
        assert settings_module.settings.router_rules == original
        assert not (tmp_path / "settings.json").exists()
    finally:
        object.__setattr__(settings_module.settings, "router_rules", original)
