# Changelog

All notable changes to FreeFlow are documented here.

---

## [2.1.1] (9 July 2026)

### Changed

- Quiet notifications by default: routine state changes (mode, language, translate, session on/off, pause) no longer toast; the floating gadget already shows them. Errors, undo confirmations, offline fallback, and "Meeting notes saved" still notify.
- New tray "Notifications" setting: All / Important only (default) / Off, persisted as `notify_level` in `settings.json`.

### Added

- README usage guides for voice editing ("edit this"), meeting notes, and the notification levels.

---

## [2.1.0] (8 July 2026)

### Added

- Voice editing of selected text: select text anywhere, hold the hotkey and say "edit this: make it more formal" (also "rewrite this", "edit selection"). The selection is rewritten by the LLM and pasted in place, undo-able.
- Meeting notes mode: tray toggle records the microphone in 60-second chunks, transcribes them in the background, and on stop writes and opens a markdown file with a summary (bullets, decisions, action items) and the full transcript.
- Offline transcription fallback: when Groq is unreachable and the optional faster-whisper package is installed, utterances are transcribed locally instead of being lost.
- Latency instrumentation: every dispatch logs total/transcribe/cleanup milliseconds; the overlay briefly shows the last round-trip time after each paste.
- Language lock now defaults to English, with an overlay/tray toggle to cycle EN -> FA -> auto.
- Translate mode gated behind a guard check to stop accidental mistranslation.
- Session watchdog to recover a stuck or crashed session-mode capture.
- App-wide file logging (`app.log`, rotating, 1MB x 3 backups) in `%APPDATA%\FreeFlow\logs`, so the frozen build logs even with no console.
- pytest regression suite covering router, settings, dispatch, undo-paste and overlay processing state.
- Ten new default router rules: Outlook, Word and Thunderbird -> polished; Slack, Teams (classic and new), Discord and WhatsApp -> note; Notion -> brand_voice; Gmail/Outlook web titles -> polished; X/Twitter titles -> note.
- Undo last paste: say "undo paste" or "scratch paste", or use the tray menu. Reverses the most recent paste within a 120s window (2000-character cap).
- Live elapsed-time suffix on the overlay's processing state once a cleanup round-trip passes one second.
- `settings.json` schema validation: unknown keys and scalar type mismatches are warned about and skipped, rather than silently misapplied.
- Branded FreeFlow icon baked into the frozen build.

### Changed

- `_dispatch` refactored from one large function into a staged pipeline with a single shared finalisation step, fixing a latent bug where the overlay's RAW badge could go stale on a short-circuit branch.
- Cleanup-fallback tray toast throttled to once per ten minutes to avoid spam during a run of degraded Groq responses.

---

## 2.0.x (July 2026)

### Added

- Windows installer: no-admin build option, per-user desktop shortcut.
- User data (`dictionary.json`, `snippets.json`, `settings.json`, `config.json`) moved to `%APPDATA%\FreeFlow`, so app updates never wipe customisations.
- Single-instance guard: a named mutex stops a second launch from double-running.
- About window, Help/Report tray items, and a first-run welcome window for member onboarding.

### Fixed

- English speech no longer transcribed as Persian: the Whisper glossary bias that caused this is off by default, and `dictation_language` now locks correctly.

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
