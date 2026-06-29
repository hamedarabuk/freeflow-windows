# Changelog

All notable changes to FreeFlow are documented here.

---

## [2.0.0] — 2026-06-30

### Added

- **Automatic data backups.** Your `dictionary.json` and `snippets.json` are backed up on every startup if they have changed since the last backup. No manual action needed; your customisations are safe across reinstalls.
- **Quality guard.** A lightweight check runs on every cleanup result and rejects output that looks like garbage (too short, too different from the raw transcript, or truncated). When cleanup fails the guard, FreeFlow falls back to the raw Whisper transcript automatically, so nothing is ever lost.
- **First-run API key wizard.** On first launch, a small setup dialog asks you to paste your free Groq API key. The key is stored privately in `%APPDATA%\FreeFlow\config.json` and survives reinstalls. You never need to edit a config file by hand.
- **No-admin input backend (experimental).** A `pynput`-based hold-to-talk path is available for users who cannot or do not want to run FreeFlow as administrator. Enable it by setting `"input_backend": "pynput"` in `settings.json`. See the Customise section in README for details.
- **Quality eval harness.** A developer harness (`tests/eval/run_eval.py`) measures Word Error Rate on a sample set so accuracy changes can be tracked across versions.
- **Windows installer.** A one-click `FreeFlow-Setup.exe` installer (built with PyInstaller + Inno Setup) is now the recommended install path for members. No Python required.
- **Startup version check.** FreeFlow silently checks for a newer version on startup and shows a one-line tray notice if one is available. No personal data is sent; the check is a single unauthenticated HTTP GET.
- **`version.py` and `version.json`.** Canonical version string and the remote release manifest, used by the version checker and PyInstaller build.

### Changed

- **Four cleanup modes improved.** `polished`, `brand_voice`, `note`, and `raw` modes have been refined with better prompt wording and stricter AI-cliché suppression.
- **Portable brand voice.** The `brand_voice` mode now reads `brand_name` and `brand_voice_notes` from `settings.json`, so different members get their own brand voice without editing prompt files.
- **Session mode dispatch queue.** Continuous session-mode bursts are now serialised through a dedicated worker thread so rapid speech does not produce garbled overlapping pastes.
- **Translate toggle made idempotent.** Double-firing the translate button no longer flips the toggle back off unexpectedly.

### Fixed

- **Transcription accuracy.** Hallucinated filler tokens (`[BLANK_AUDIO]`, `[Music]`, etc.) and very-low-probability segments are now filtered before cleanup so junk never reaches the LLM.
- **Raw-fallback reliability.** The cleanup step now falls back to the raw transcript cleanly on any Groq error or timeout, including `429 Too Many Requests`, rather than dropping the dictation silently.
- **pywin32 post-install.** The developer install guide now documents the `pywin32_postinstall.py` step that fixes `No module named 'win32gui'`.
- **Inline formatting (new paragraph / new line).** Multi-segment dictations with spoken line-break commands now clean each segment independently and rejoin with the correct number of real newlines.
