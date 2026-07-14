"""test_brand_name.py: the first-run welcome dialog persists an optional
brand name for Brand voice mode via settings.set_brand_name."""

from __future__ import annotations

import settings as settings_module


def test_set_brand_name_updates_singleton_and_persists(monkeypatch):
    saved = {}

    def _fake_save(key, value):
        saved[key] = value

    monkeypatch.setattr(settings_module, "save_setting", _fake_save)
    original = settings_module.settings.brand_name
    try:
        settings_module.set_brand_name("Aurelia Atelier")
        assert settings_module.settings.brand_name == "Aurelia Atelier"
        assert saved == {"brand_name": "Aurelia Atelier"}
    finally:
        object.__setattr__(settings_module.settings, "brand_name", original)
