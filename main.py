"""
main.py — entry point for the push-to-talk dictation service.

Hotkey:
  Alt+1   hold-to-talk (press = start recording, release = dispatch)

Mode switching and translate-to-English live on the floating gadget,
not on a hotkey: clicking the central mode pill opens a dropdown with
Auto, per-mode overrides, the Translate-to-British-English toggle, and
Quit. An earlier modifier+digit cycle hotkey was removed because the
`keyboard` package's modifier+digit hooks are unreliable on Windows.

Config: loaded from .env at the repo root via config.py.
Transcription + cleanup: Groq only (no openai SDK, no other subscriptions).
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import keyboard
import pyperclip

# ---------------------------------------------------------------------------
# Optional pynput backend
# ---------------------------------------------------------------------------
# EXPERIMENTAL: when settings.input_backend == "pynput", hold-to-talk uses
# pynput.keyboard.Listener (press/release of Alt+1) instead of the keyboard
# library.  This avoids the admin-rights requirement that blocks non-technical
# users.  The default is "keyboard"; pynput must be installed separately
# (see requirements-optional.txt).  The keyboard library's global hooks are
# only registered when input_backend == "keyboard", so importing this module
# does not pull in keyboard unless needed.
_PYNPUT_ACTIVE = False
_pynput_listener = None  # type: ignore[var-annotated]
# ---------------------------------------------------------------------------

from paths import user_file, resource_file, migrate_legacy_user_data

migrate_legacy_user_data()

from backup import backup_if_changed
from config import load_config
from cleanup import clean, edit_text, get_last_error as cleanup_last_error, startup_selfcheck
from dictionary import apply_substitutions
from history import append, last_ten, log_dir
from paste import paste_text
from meeting import MeetingRecorder, write_meeting_notes
from recorder import Recorder
from router import pick_mode, _get_foreground_info
from snippets import expand_snippet
from settings import settings, set_dictation_language, add_router_rule as settings_add_router_rule
import transcribe as _transcribe_module
from transcribe import transcribe
from tray import TrayIcon
from overlay import Overlay, load_state as _load_overlay_state

# Session mode (VAD-driven continuous capture). Imported lazily inside
# the toggle handler so a missing webrtcvad install doesn't block startup
# of the regular hold-to-talk path.

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
)

# File logging: pythonw and the frozen (PyInstaller) build have no console,
# so without a file handler the installed app logs nothing at all. Rotating
# handler in %APPDATA%\FreeFlow\logs; failure to attach must never block
# startup (for example a locked file from a second instance).
try:
    from logging.handlers import RotatingFileHandler

    from paths import logs_dir as _logs_dir

    _file_handler = RotatingFileHandler(
        _logs_dir() / "app.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logging.getLogger().addHandler(_file_handler)
except Exception:
    pass

log = logging.getLogger("dictation.main")

_tray: Optional[TrayIcon] = None
_overlay: Optional[Overlay] = None
_cfg = load_config()
_paused = False
_forced_mode: Optional[str] = None
_forced_mode_lock = threading.Lock()
_translate_to_english: bool = False
_translate_lock = threading.Lock()
_detected_language: str = ""
_recording_active = False
_recording_lock = threading.Lock()

# Set True whenever a KEY_DOWN of the hotkey char is claimed as part of the
# Alt+<hotkey> combo (modifier held), so the matching KEY_UP is suppressed
# too. Keeps a bare, un-modified press of the hotkey char typing normally.
# Sticky for the whole hold: once claimed, every KEY_DOWN repeat is
# suppressed without re-checking is_pressed(modifier) (see _on_alt1_press),
# so a mid-hold Alt-release race can never leak the raw char.
_hotkey_claimed = False

# Live audio level (0..~1), written by the audio thread, read by the overlay
# poll loop. Single-writer / single-reader, atomic in CPython so no lock needed.
_current_audio_level: float = 0.0

# Undo-last-paste state: character count of the most recent paste (Enter
# keystrokes count as one character each) and the monotonic time it happened.
# Single-writer / single-reader per dispatch, no lock needed.
_last_paste_len: int = 0
_last_paste_ts: float = 0.0

# Rolling latency window: ms_total of the last 20 completed cleaned_paste
# dispatches. Single-writer (dispatch thread) / single-reader (overlay poll),
# no lock needed, same convention as _current_audio_level above.
_recent_latency_ms: "deque[int]" = deque(maxlen=20)


def get_recent_latency_ms() -> Optional[tuple[int, int]]:
    """Return (last, avg) ms_total over the rolling window of recent
    cleaned_paste dispatches, or None when the window is empty."""
    if not _recent_latency_ms:
        return None
    last = _recent_latency_ms[-1]
    avg = int(sum(_recent_latency_ms) / len(_recent_latency_ms))
    return last, avg


def _on_audio_level(rms: float) -> None:
    global _current_audio_level
    _current_audio_level = rms


_recorder = Recorder(level_callback=_on_audio_level)

# Session mode state. Double-tap Alt+1 to enter, double-tap again to exit.
_session_active = False
_session_lock = threading.Lock()
_session = None  # type: ignore[var-annotated]
_press_start_time: float = 0.0
_last_tap_release_time: float = 0.0
_DOUBLE_TAP_WINDOW_MS = settings.double_tap_window_ms
_SHORT_TAP_MAX_MS = settings.short_tap_max_ms

# Session-mode burst dispatch queue. Bursts arrive from the VAD audio thread
# and must be processed strictly in order: parallel dispatches race on the
# shared clipboard (paste_text uses pyperclip.copy + keyboard.send Ctrl+V,
# and a second copy clobbers the first before its paste fires, producing
# garbled output). A single worker thread drains the queue.
_session_dispatch_queue: "queue.Queue[Optional[Path]]" = queue.Queue()
_session_worker: Optional[threading.Thread] = None
_session_worker_stop = threading.Event()

# Meeting notes mode state (mic-only continuous capture, chunked
# transcription). Toggled from the tray menu. _meeting_finishing covers the
# window between "stop" being clicked and meeting-notes.md actually being
# written (pending transcriptions + summary), during which a further click
# is refused rather than starting a second recorder.
_meeting_active = False
_meeting_finishing = False
_meeting_lock = threading.Lock()
_meeting_recorder: Optional[MeetingRecorder] = None


def _restore_persisted_state() -> None:
    """Load forced_mode and translate_to_english from .overlay-state.json on startup."""
    global _forced_mode, _translate_to_english
    persisted = _load_overlay_state()
    fm = persisted.get("forced_mode")
    if fm in {"polished", "brand_voice", "prompt", "note", "raw"}:
        _forced_mode = fm
    tr = persisted.get("translate_to_english")
    if isinstance(tr, bool):
        _translate_to_english = tr


def _current_auto_mode() -> str:
    process_name, window_title = _get_foreground_info()
    return pick_mode(process_name, window_title)


def _on_pause_toggle() -> None:
    global _paused
    _paused = not _paused
    if _tray:
        _tray.set_paused(_paused)
        _tray.notify("Paused" if _paused else "Resumed")
    if _overlay:
        _overlay.set_paused(_paused)
    log.info("Dictation %s", "paused" if _paused else "resumed")


def _on_force_mode(mode: Optional[str]) -> None:
    """Persistent override. mode=None means 'return to Auto (router-driven)'."""
    global _forced_mode
    with _forced_mode_lock:
        _forced_mode = mode
    if _tray:
        _tray.notify("Mode: Auto" if mode is None else f"Mode locked: {mode}")
    if _overlay:
        _overlay.set_forced(mode)
    log.info("Forced mode set to: %s", mode)


def _on_set_translate(on: bool) -> None:
    """Set the translate-to-English flag to a specific value.

    Absolute, not toggle: if the menu accidentally fires twice (CTk
    double-fire pattern seen in drag), two calls with the same target
    value remain idempotent. A toggle would flip back and net to zero
    change, which was the 'button stuck on' bug."""
    global _translate_to_english
    on = bool(on)
    with _translate_lock:
        if _translate_to_english == on:
            return
        _translate_to_english = on
    if _tray:
        _tray.notify("Translate: ON" if on else "Translate: OFF")
    if _overlay:
        _overlay.set_translate(on)
    log.info("Translate-to-English: %s", on)


# Dictation-language lock cycle order: en -> fa -> auto -> en.
_DICTATION_LANGUAGE_ORDER = ("en", "fa", "auto")


def _set_dictation_language(lang: str) -> None:
    """Set the dictation-language lock to an explicit value.

    Shared helper used by both the overlay's language-pill click-cycle and
    the tray's Language submenu radio items, so the two controls never
    drift out of sync.
    """
    set_dictation_language(lang)
    if _tray:
        _tray.refresh_language()
        _tray.notify(f"Dictation language: {lang.upper()}")


def _cycle_dictation_language() -> None:
    """Cycle the dictation-language lock: en -> fa -> auto -> en."""
    try:
        idx = _DICTATION_LANGUAGE_ORDER.index(settings.dictation_language)
    except ValueError:
        idx = -1
    next_lang = _DICTATION_LANGUAGE_ORDER[(idx + 1) % len(_DICTATION_LANGUAGE_ORDER)]
    _set_dictation_language(next_lang)


def _on_show_last() -> None:
    records = last_ten()
    if not records:
        if _tray:
            _tray.notify("No dictation history today.", important=True)
        return
    path = log_dir() / f"{time.strftime('%Y-%m-%d')}.jsonl"
    os.startfile(str(path))


def _on_open_logs() -> None:
    os.startfile(str(log_dir()))


def _copy_recent_dictation(text: str) -> None:
    """Copy a past dictation to the clipboard (tray 'Copy recent dictation').

    Copy, not re-paste: after a tray-menu click the foreground window is not
    reliably the one the user means, so pasting would spray text somewhere
    arbitrary. The clipboard puts the user in control of the destination."""
    try:
        pyperclip.copy(text)
        if _tray:
            _tray.notify("Copied to clipboard. Ctrl+V where you want it.")
    except Exception as exc:
        log.warning("copy recent dictation failed: %s", exc)


def _last_dictated_app() -> str:
    """Process name of the most recent dictation today (e.g. 'code.exe').

    Read from the history log rather than the live foreground window,
    because at tray-menu time the foreground is the taskbar, not the app
    the user has in mind. Survives restarts for free."""
    records = last_ten()
    for rec in reversed(records):
        app = (rec.get("app_process") or "").strip()
        if app:
            return app
    return ""


def _route_last_app(mode: str) -> None:
    """Persist a routing rule: the most recent dictation's app always gets
    *mode*. One-click fix for 'it picked the wrong mode in this app'."""
    app = _last_dictated_app()
    if not app:
        if _tray:
            _tray.notify("No dictation yet today, nothing to route.", important=True)
        return
    try:
        settings_add_router_rule(app, mode)
        if _tray:
            _tray.notify(f"{app} will now always use {mode}.", important=True)
    except Exception as exc:
        log.warning("route last app failed: %s", exc)
        if _tray:
            _tray.notify("Could not save the routing rule; see the log.", important=True)


def _ensure_user_config(name: str) -> Path:
    """Return <name>.json from the stable per-user data directory. If missing,
    seed from the bundled .example resource.

    Guard: only creates the file when it is genuinely absent. Never overwrites,
    truncates, or resets an existing user file.
    """
    target = user_file(f"{name}.json")
    example = resource_file(f"{name}.json.example")
    if not target.exists() and example.exists():
        try:
            target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("Seeded %s from example", target.name)
        except Exception as exc:
            log.warning("Failed to seed %s: %s", target.name, exc)
    return target


def _on_edit_dictionary() -> None:
    # Runs on the pystray tray thread. Creating a Tk/CTk window here would
    # crash the app, so marshal the open onto the main Tk loop via the
    # overlay's root.after(). Fall back to the raw file only if no root yet.
    _ensure_user_config("dictionary")
    root = _overlay.tk_root if _overlay else None
    if root is not None:
        from dictionary_editor import open_dictionary_editor
        root.after(0, lambda: open_dictionary_editor(root))
    else:
        os.startfile(str(_ensure_user_config("dictionary")))


def _on_about() -> None:
    # Runs on the pystray tray thread. Creating a Tk/CTk window here would
    # crash the app, so marshal the open onto the main Tk loop via the
    # overlay's root.after(). Fall back to the website only if no root yet.
    root = _overlay.tk_root if _overlay else None
    if root is not None:
        from about_window import open_about_window
        root.after(0, lambda: open_about_window(root))
    else:
        import webbrowser
        from about import WEBSITE_URL
        webbrowser.open(WEBSITE_URL)


def _on_edit_snippets() -> None:
    # Runs on the pystray tray thread. Creating a Tk/CTk window here would
    # crash the app, so marshal the open onto the main Tk loop via the
    # overlay's root.after(). Fall back to the raw file only if no root yet.
    _ensure_user_config("snippets")
    root = _overlay.tk_root if _overlay else None
    if root is not None:
        from snippets_editor import open_snippets_editor
        root.after(0, lambda: open_snippets_editor(root))
    else:
        os.startfile(str(_ensure_user_config("snippets")))


def _on_quit() -> None:
    log.info("Quit requested.")
    if _overlay:
        _overlay.stop()
    if _tray:
        _tray.stop()
    if _PYNPUT_ACTIVE and _pynput_listener is not None:
        try:
            _pynput_listener.stop()
        except Exception:
            pass
    else:
        keyboard.unhook_all()
    sys.exit(0)


def _on_hide_gadget() -> None:
    log.info("Gadget hidden (tray still running; click tray icon to restore).")
    if _overlay:
        _overlay.hide()
    if _tray:
        _tray.notify("Gadget hidden. Click the tray icon to restore.", important=True)


def _on_show_gadget() -> None:
    log.info("Showing gadget.")
    if _overlay:
        _overlay.show()


def _on_compact_toggle() -> None:
    """Shared handler for the gadget mode-menu 'Compact mode' item and the
    tray 'Compact gadget' item: flips the overlay's compact flag and keeps
    the tray checkmark in sync whichever surface triggered it."""
    if _overlay:
        _overlay.set_compact(not _overlay.get_compact())
        log.info("Compact mode %s", "ON" if _overlay.get_compact() else "OFF")
    if _tray:
        _tray.refresh_compact_state()


def _post_dispatch_state() -> str:
    """Return the overlay state to restore after a dispatch completes.

    In session mode the stream stays open and the indicator must return to
    'session' (showing the equaliser and keeping the eq-tick alive) so the
    user can see the mic is still active between utterances. In meeting
    notes mode it returns to 'meeting' for the same reason. In hold-to-talk
    mode the correct state is 'idle'."""
    if _session_active:
        return "session"
    if _meeting_active:
        return "meeting"
    return "idle"


def _on_session_burst(wav_path: Path) -> None:
    """SessionManager calls this from the audio thread for each finalised
    speech burst. Enqueue and return immediately; a single worker thread
    drains the queue so dispatches stay strictly serial and the audio
    callback is never blocked."""
    _session_dispatch_queue.put(wav_path)


def _session_worker_loop() -> None:
    """Drain the session dispatch queue strictly in order. Sentinel value
    None tells the worker to exit.

    The whole iteration body is guarded so no unexpected exception can kill
    the thread silently: a dead worker used to stall session mode with no
    visible symptom (bursts queued, nothing pasted)."""
    while not _session_worker_stop.is_set():
        try:
            try:
                wav_path = _session_dispatch_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if wav_path is None:
                return
            try:
                _dispatch(wav_path)
            except Exception as exc:
                log.exception("Session burst dispatch failed: %s", exc)
        except Exception as exc:
            log.exception("Session worker iteration failed: %s", exc)
            time.sleep(0.5)


def _start_session_worker() -> None:
    global _session_worker
    if _session_worker is not None and _session_worker.is_alive():
        return
    # Drain any leftover items from a previous session before restart.
    while not _session_dispatch_queue.empty():
        try:
            _session_dispatch_queue.get_nowait()
        except queue.Empty:
            break
    _session_worker_stop.clear()
    _session_worker = threading.Thread(
        target=_session_worker_loop, daemon=True, name="dictation-session-worker"
    )
    _session_worker.start()


def _stop_session_worker() -> None:
    _session_worker_stop.set()
    _session_dispatch_queue.put(None)


def _session_watchdog_loop() -> None:
    """Liveness watchdog for the session worker thread.

    Every 30 seconds: if session mode is active but the worker thread has
    died, start a replacement so queued bursts resume instead of stalling
    silently. Runs as a daemon thread for the lifetime of the app."""
    while True:
        time.sleep(30)
        try:
            with _session_lock:
                if not _session_active:
                    continue
                if _session_worker is None or not _session_worker.is_alive():
                    log.warning(
                        "Session worker thread dead while session active; restarting."
                    )
                    _start_session_worker()
        except Exception as exc:
            log.exception("Session watchdog iteration failed: %s", exc)


def _start_session_watchdog() -> None:
    threading.Thread(
        target=_session_watchdog_loop,
        daemon=True,
        name="dictation-session-watchdog",
    ).start()


def _on_session_toggle() -> None:
    """Start or stop session mode."""
    global _session_active, _session
    with _session_lock:
        if _session_active:
            if _session is not None:
                try:
                    _session.stop()
                except Exception as exc:
                    log.warning("Session stop failed: %s", exc)
                _session = None
            _stop_session_worker()
            _session_active = False
            if _tray:
                _tray.notify("Session: OFF")
            if _overlay:
                _overlay.set_state("idle")
            log.info("Session mode OFF")
            return
        # Start
        try:
            from vad import SessionManager, is_available
        except ImportError as exc:
            log.error("Session import failed: %s", exc)
            if _tray:
                _tray.notify("Session needs webrtcvad-wheels. Install + restart.", important=True)
            return
        if not is_available():
            if _tray:
                _tray.notify("webrtcvad-wheels not installed. pip install webrtcvad-wheels", important=True)
            return
        try:
            _start_session_worker()
            _session = SessionManager(
                on_burst=_on_session_burst,
                level_callback=_on_audio_level,
            )
            _session.start()
            _session_active = True
            if _tray:
                _tray.notify("Session: ON (double-tap Alt+1 to exit)")
            if _overlay:
                _overlay.set_state("session")
            log.info("Session mode ON")
        except Exception as exc:
            log.error("Session start failed: %s", exc)
            _stop_session_worker()
            _session = None
            _session_active = False
            if _tray:
                _tray.notify(f"Session start failed: {exc}", important=True)


def _finish_meeting_notes(recorder: MeetingRecorder) -> None:
    """Background-thread tail of a meeting-notes stop: waits for pending
    transcriptions, summarises, writes meeting-notes.md, opens it, and
    notifies the tray. Runs off the tray-click thread since it can block
    for up to ~120s (pending transcriptions) plus the summary call."""
    global _meeting_active, _meeting_finishing, _meeting_recorder
    try:
        output_path = write_meeting_notes(recorder, _cfg.groq_api_key)
        os.startfile(str(output_path))
        if _tray:
            _tray.notify("Meeting notes saved", important=True)
        log.info("Meeting notes saved: %s", output_path)
    except Exception as exc:
        log.error("Meeting notes finish failed: %s", exc)
        if _tray:
            _tray.notify(f"Meeting notes failed: {exc}", important=True)
    finally:
        with _meeting_lock:
            _meeting_active = False
            _meeting_finishing = False
            _meeting_recorder = None
        if _tray:
            _tray.refresh_meeting_state()
            _tray.set_idle()
        if _overlay:
            _overlay.set_state(_post_dispatch_state())


def _on_meeting_toggle() -> None:
    """Start or stop meeting notes mode (mic-only continuous capture).

    Refuses to start a second recording whilst one is active or still
    finishing: the tray menu label only ever offers "Start" when
    _meeting_active is False, and this guard covers the race where a click
    lands before the label refreshes."""
    global _meeting_active, _meeting_finishing, _meeting_recorder
    with _meeting_lock:
        if _meeting_finishing:
            if _tray:
                _tray.notify("Finishing transcription...", important=True)
            return
        if _meeting_active:
            recorder = _meeting_recorder
            _meeting_finishing = True
            if _tray:
                _tray.notify("Finishing transcription...", important=True)
            if _overlay:
                _overlay.set_state("processing")
            threading.Thread(
                target=_finish_meeting_notes, args=(recorder,), daemon=True
            ).start()
            return

        try:
            recorder = MeetingRecorder(api_key=_cfg.groq_api_key, level_callback=_on_audio_level)
            recorder.start()
        except Exception as exc:
            log.error("Meeting notes start failed: %s", exc)
            if _tray:
                _tray.notify(f"Meeting notes start failed: {exc}", important=True)
            return

        _meeting_recorder = recorder
        _meeting_active = True
        _meeting_finishing = False
        if _tray:
            _tray.refresh_meeting_state()
            _tray.notify("Meeting notes: recording")
        if _overlay:
            _overlay.set_state("meeting")
        log.info("Meeting notes started: %s", recorder.session_dir)


def _normalise_cmd(text: str) -> str:
    s = " ".join(text.lower().split())
    while s and s[-1] in ".,;:!?":
        s = s[:-1]
    return s


def _match_voice_command(text: str):
    """Return (action, value) if text is an exact whole-transcript command match, else None."""
    key = _normalise_cmd(text)
    for cmd in settings.voice_commands:
        phrases = cmd.get("phrases") or []
        for phrase in phrases:
            if key == _normalise_cmd(str(phrase)):
                return cmd.get("action"), cmd.get("value")
    return None


def _match_capture_command(text: str):
    """Return the payload string if text starts with a capture-command trigger phrase, else None.

    The trigger phrase (and any immediately following colon, comma or whitespace)
    is stripped from the start of the normalised transcript to yield the payload.
    Returns None when no trigger phrase matches, or an empty string when the
    utterance contained only the trigger phrase (caller should ignore empty payloads).
    """
    normalised = _normalise_cmd(text)
    for phrase in settings.capture_commands:
        trigger = _normalise_cmd(str(phrase))
        if not trigger:
            continue
        if normalised == trigger:
            # Utterance was only the trigger phrase; return empty string so
            # the caller can skip gracefully.
            return ""
        if normalised.startswith(trigger):
            # Strip trigger plus any leading punctuation / whitespace separator.
            rest = normalised[len(trigger):]
            rest = rest.lstrip(" :,")
            return rest
    return None


# Guard against pasting an enormous selection into the LLM (cost + latency).
_EDIT_SELECTION_MAX_CHARS = 8000


def _match_edit_command(text: str):
    """Return the instruction string if text starts with an edit-command
    trigger phrase (e.g. "edit this: make it more formal"), else None.

    Same trigger-phrase-plus-payload-strip convention as
    _match_capture_command: the trigger phrase (and any immediately following
    colon, comma or whitespace) is stripped to yield the instruction. Returns
    an empty string when the utterance was only the trigger phrase.
    """
    normalised = _normalise_cmd(text)
    for phrase in settings.edit_commands:
        trigger = _normalise_cmd(str(phrase))
        if not trigger:
            continue
        if normalised == trigger:
            return ""
        if normalised.startswith(trigger):
            rest = normalised[len(trigger):]
            rest = rest.lstrip(" :,")
            return rest
    return None


def _capture_selection() -> tuple[Optional[str], str]:
    """Capture the current text selection via a clipboard round-trip.

    Saves the existing clipboard, clears it, sends Ctrl+C, then reads the
    clipboard back after a brief wait (retrying once for slower apps: 0.15s,
    then a further 0.15s if still empty). Always returns the saved clipboard
    alongside the result so the caller can restore it on any outcome that
    does not go on to paste.

    Returns (selection, old_clipboard). selection is None if nothing was
    selected after the retry.
    """
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        old_clipboard = ""
    try:
        pyperclip.copy("")
    except Exception:
        pass
    keyboard.send("ctrl+c")
    time.sleep(0.15)
    try:
        selection = pyperclip.paste()
    except Exception:
        selection = ""
    if not selection:
        time.sleep(0.15)
        try:
            selection = pyperclip.paste()
        except Exception:
            selection = ""
    return (selection or None), old_clipboard


def _build_inline_pattern():
    """Return a compiled regex that matches any inline formatting phrase.

    Builds once per call; callers are expected to cache the result if called
    in a tight loop, but in practice this runs once per dispatch.
    """
    all_phrases = []
    for entry in settings.inline_formatting:
        for phrase in entry.get("phrases") or []:
            all_phrases.append(re.escape(str(phrase)))
    if not all_phrases:
        return None
    joined = "|".join(all_phrases)
    return re.compile(
        r"(?<![a-zA-Z])(" + joined + r")(?![a-zA-Z])",
        re.IGNORECASE,
    )


def _split_inline_formatting(text: str):
    """Split *text* at inline formatting phrases.

    Returns a list of (segment_text, break_newlines) pairs where
    break_newlines is the number of \\n characters that should follow that
    segment.  The final entry always has break_newlines=0.

    Returns None when no inline formatting phrase is found (fast path).
    """
    pattern = _build_inline_pattern()
    if pattern is None:
        return None

    # Find the first match to decide whether to bother splitting at all.
    if not pattern.search(text):
        return None

    # Build a lookup: normalised phrase -> newline count.
    phrase_to_newlines: dict[str, int] = {}
    for entry in settings.inline_formatting:
        nl = int(entry.get("newlines") or 1)
        for phrase in entry.get("phrases") or []:
            phrase_to_newlines[_normalise_cmd(str(phrase))] = nl

    segments = []
    pos = 0
    for m in pattern.finditer(text):
        segment = text[pos:m.start()].strip(" ,")
        matched_phrase = _normalise_cmd(m.group(1))
        nl = phrase_to_newlines.get(matched_phrase, 1)
        segments.append((segment, nl))
        pos = m.end()

    # Remainder after the last match.
    tail = text[pos:].strip(" ,")
    segments.append((tail, 0))
    return segments


def _paste_with_breaks(text: str) -> None:
    """Inject *text* so that embedded \\n characters become real Enter presses.

    Splits on \\n; for each non-empty part calls paste_text, then sends
    keyboard Enter once per newline separator.  Handles leading/trailing
    empty parts (an utterance that resolves to pure newlines still produces
    the correct number of Enter presses).

    Records the total character count that reached the target (pasted
    characters plus one per Enter keystroke) and the time, for undo-last-paste.
    """
    global _last_paste_len, _last_paste_ts
    parts = text.split("\n")
    total_chars = 0
    for i, part in enumerate(parts):
        if part:
            paste_text(part)
            total_chars += len(part)
        if i < len(parts) - 1:
            keyboard.send("enter")
            total_chars += 1
    _last_paste_len = total_chars
    _last_paste_ts = time.monotonic()


def _undo_last_paste() -> None:
    """Undo the most recently pasted text by sending one backspace per
    character (an Enter keystroke counts as one character). Guarded by a
    120s window so a stray "undo paste" long after the fact cannot wipe out
    unrelated typing done in between."""
    global _last_paste_len
    if _last_paste_len == 0:
        if _tray:
            _tray.notify("Nothing to undo", important=True)
        log.info("Undo paste: nothing to undo")
        return
    elapsed = time.monotonic() - _last_paste_ts
    if elapsed > 120:
        if _tray:
            _tray.notify("Undo window expired", important=True)
        log.info("Undo paste: window expired (%.1fs)", elapsed)
        return
    n = min(_last_paste_len, 2000)
    for _ in range(n):
        keyboard.send("backspace")
    _last_paste_len = 0
    if _tray:
        _tray.notify("Paste undone", important=True)
    log.info("Undo paste: sent %d backspaces", n)


# ---------------------------------------------------------------------------
# Dispatch pipeline
# ---------------------------------------------------------------------------
# _dispatch runs a fixed sequence of stages against one recorded burst. Each
# stage inspects the shared _DispatchContext and either returns None (meaning
# "not my branch, carry on") or an _Outcome (a terminal result). The first
# stage to return an _Outcome wins; the cleanup stage always returns one, so
# every dispatch ends with exactly one _Outcome. That single outcome flows
# through _finalise(), which owns the tray + overlay state reset. Previously
# every branch reset the tray and overlay itself, and fixes to one branch kept
# missing the others (the recurring source of the badge/state regressions).
# Centralising the reset here means it happens once, the same way, always.


@dataclass
class _DispatchContext:
    """Mutable per-dispatch state, threaded through the pipeline stages.

    One instance lives for the duration of a single _dispatch call on the
    calling thread only, so it needs no locking of its own. The forced-mode
    and translate globals it snapshots are still read under their existing
    locks in _resolve_context."""

    wav_path: Path
    t_start: float
    process_name: str = ""
    window_title: str = ""
    mode_auto: str = "polished"
    mode_forced: Optional[str] = None
    effective_mode: str = "polished"
    translate_flag: bool = False
    ms_record: int = 0
    ms_transcribe: int = 0
    language: str = ""
    text_raw: str = ""
    t_rec_end: float = 0.0
    t_transcribe_end: float = 0.0


@dataclass
class _Outcome:
    """Terminal result of the pipeline. Carries only what _finalise needs.

    kind is a short label for logs and tests. tray_notify, when set, is a
    balloon shown straight after the idle reset (used for the transcription
    failure path). fallback_badge drives the overlay RAW badge."""

    kind: str
    tray_notify: Optional[str] = None
    fallback_badge: bool = False


def _log_latency(ctx: _DispatchContext, ms_cleanup: int, ms_total: int) -> None:
    """Emit the structured latency line for a completed paste outcome
    (cleaned_paste, snippet, voice command). ms_cleanup is 0 where the
    cleanup stage did not run. Uses the timings already tracked on *ctx*;
    no new timers on the hot path."""
    log.info(
        "latency ms_total=%d ms_transcribe=%d ms_cleanup=%d mode=%s",
        ms_total, ctx.ms_transcribe, ms_cleanup, ctx.effective_mode,
    )


def _append_history(
    ctx: _DispatchContext,
    *,
    transcript_clean: str,
    ms_cleanup: int,
    ms_total: int,
    fallback: bool,
) -> None:
    """Write one dictation history record. The invariant fields come from
    *ctx*; only transcript_clean, the cleanup latency, the total latency and
    the fallback flag vary per branch. Wrapped exactly as each branch wrapped
    it before, so a logging failure never breaks the paste path."""
    try:
        append(
            mode_auto=ctx.mode_auto,
            mode_forced=ctx.mode_forced,
            language=ctx.language,
            transcript_raw=ctx.text_raw,
            transcript_clean=transcript_clean,
            app_process=ctx.process_name,
            app_title=ctx.window_title,
            ms_record=ctx.ms_record,
            ms_transcribe=ctx.ms_transcribe,
            ms_cleanup=ms_cleanup,
            ms_total=ms_total,
            fallback=fallback,
        )
    except Exception as exc:
        log.warning("History append failed: %s", exc)


def _resolve_context(ctx: _DispatchContext) -> None:
    """Populate the routing and record-latency fields on *ctx*.

    Reads the forced-mode and translate globals under their existing locks;
    the critical sections stay exactly as narrow as before (a single guarded
    read each)."""
    ctx.process_name, ctx.window_title = _get_foreground_info()
    ctx.mode_auto = pick_mode(ctx.process_name, ctx.window_title)

    # Persistent override (v2): do NOT clear _forced_mode after the dispatch.
    with _forced_mode_lock:
        ctx.mode_forced = _forced_mode

    ctx.effective_mode = ctx.mode_forced if ctx.mode_forced else ctx.mode_auto

    with _translate_lock:
        ctx.translate_flag = _translate_to_english

    ctx.t_rec_end = time.monotonic()
    ctx.ms_record = int((ctx.t_rec_end - ctx.t_start) * 1000)


def _stage_transcribe(ctx: _DispatchContext) -> Optional[_Outcome]:
    """Transcribe the burst. Terminal on failure (badge cleared, failure
    toast) or on an empty transcript (silence/noise/hallucination filter).
    Otherwise stores the transcript on *ctx* and returns None to continue."""
    global _detected_language, _last_offline_toast_ts
    try:
        text_raw, language = transcribe(ctx.wav_path, _cfg.groq_api_key)
    except Exception as exc:
        log.error("Transcription failed: %s", exc)
        return _Outcome(kind="transcribe_error", tray_notify="Transcription failed.")
    finally:
        try:
            ctx.wav_path.unlink(missing_ok=True)
        except Exception:
            pass

    # Track detected language for the overlay's language pill.
    _detected_language = (language or "")[:2].lower()
    ctx.language = language
    ctx.text_raw = text_raw

    if _transcribe_module.last_call_used_offline:
        now = time.monotonic()
        if _tray and now - _last_offline_toast_ts >= _OFFLINE_TOAST_INTERVAL_S:
            _last_offline_toast_ts = now
            _tray.notify("Offline transcription", important=True)

    ctx.t_transcribe_end = time.monotonic()
    ctx.ms_transcribe = int((ctx.t_transcribe_end - ctx.t_rec_end) * 1000)

    # Empty transcript means transcribe() dropped a silent or hallucinated
    # burst. Skip the cleanup + paste round-trip and return to idle.
    if not text_raw.strip():
        log.info(
            "Skipped dispatch: empty transcript (silence/noise/hallucination filter)"
        )
        return _Outcome(kind="empty_transcript")

    return None


def _stage_capture_command(ctx: _DispatchContext) -> Optional[_Outcome]:
    """Capture-command check: utterances starting with a capture trigger
    phrase (e.g. "content idea ...") are routed to the external capture
    script and suppressed from paste entirely."""
    payload = _match_capture_command(ctx.text_raw)
    if payload is None:
        return None

    ms_total_capture = int((time.monotonic() - ctx.t_start) * 1000)
    if payload:
        try:
            subprocess.Popen(
                [sys.executable, settings.content_capture_script,
                 "--source", "voice", "--text", payload],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info(
                "Capture command dispatched: %r -> %r (%dms)",
                ctx.text_raw, payload, ms_total_capture,
            )
            if _tray:
                _tray.notify("Content idea captured.", important=True)
        except Exception as exc:
            log.warning("Capture command subprocess failed: %s", exc)
    else:
        log.info("Capture command: empty payload, skipping.")

    _append_history(
        ctx,
        transcript_clean=f"[capture:{payload}]",
        ms_cleanup=0,
        ms_total=ms_total_capture,
        fallback=False,
    )
    return _Outcome(kind="capture_command")


def _stage_edit_command(ctx: _DispatchContext) -> Optional[_Outcome]:
    """Edit-command check: utterances starting with an edit trigger phrase
    (e.g. "edit this: make it more formal") capture the current text
    selection, rewrite it via cleanup.edit_text, and paste the result over
    the selection. Every branch is terminal once the trigger phrase matches.
    """
    instruction = _match_edit_command(ctx.text_raw)
    if instruction is None:
        return None

    global _last_paste_len, _last_paste_ts
    kind = "edit_command"
    tray_notify: Optional[str] = None
    fallback = False

    if not instruction:
        log.info("Edit command: empty instruction, skipping.")
        kind = "edit_command_empty"
    else:
        selection, old_clipboard = _capture_selection()
        if selection is None:
            try:
                pyperclip.copy(old_clipboard)
            except Exception:
                pass
            log.info("Edit command: no text selected.")
            kind, tray_notify = "edit_command_no_selection", "No text selected"
        elif len(selection) > _EDIT_SELECTION_MAX_CHARS:
            try:
                pyperclip.copy(old_clipboard)
            except Exception:
                pass
            log.info("Edit command: selection too large (%d chars).", len(selection))
            kind, tray_notify = "edit_command_too_large", "Selection too large"
        else:
            edited = edit_text(selection, instruction, _cfg.groq_api_key)
            if edited is None:
                try:
                    pyperclip.copy(old_clipboard)
                except Exception:
                    pass
                log.warning("Edit command: edit_text failed for instruction %r", instruction)
                kind, tray_notify, fallback = "edit_command_failed", "Edit failed", True
            else:
                paste_text(edited)
                _last_paste_len = len(edited)
                _last_paste_ts = time.monotonic()
                # paste_text leaves the pasted text on the clipboard (the
                # convention every other paste path in this module follows);
                # the original clipboard is restored above only on the
                # non-paste outcomes.

    ms_total = int((time.monotonic() - ctx.t_start) * 1000)
    _append_history(
        ctx,
        transcript_clean=f"[edit:{instruction}]",
        ms_cleanup=0,
        ms_total=ms_total,
        fallback=fallback,
    )
    if tray_notify and _tray:
        _tray.notify(tray_notify, important=True)
    if kind == "edit_command":
        log.info("Dispatched edit command [%s] %dms total", instruction, ms_total)
        _log_latency(ctx, 0, ms_total)
    return _Outcome(kind=kind)


def _stage_voice_command(ctx: _DispatchContext) -> Optional[_Outcome]:
    """Voice command short-circuit: whole-transcript exact match only.
    Commands are checked before snippets so reserved phrases always win."""
    cmd_result = _match_voice_command(ctx.text_raw)
    if cmd_result is None:
        return None

    cmd_action, cmd_value = cmd_result
    ms_total_cmd = int((time.monotonic() - ctx.t_start) * 1000)
    _append_history(
        ctx,
        transcript_clean=f"[command:{cmd_action}:{cmd_value}]",
        ms_cleanup=0,
        ms_total=ms_total_cmd,
        fallback=False,
    )
    global _last_paste_len, _last_paste_ts
    if cmd_action == "text":
        paste_text(cmd_value)
        _last_paste_len = len(cmd_value)
        _last_paste_ts = time.monotonic()
    elif cmd_action == "key":
        keyboard.send(cmd_value)
    elif cmd_action == "undo_paste":
        _undo_last_paste()
    log.info("Dispatched voice command [%s:%s] %dms total", cmd_action, cmd_value, ms_total_cmd)
    _log_latency(ctx, 0, ms_total_cmd)
    return _Outcome(kind="voice_command")


def _stage_snippet(ctx: _DispatchContext) -> Optional[_Outcome]:
    """Snippet shortcut: if the transcribed text (already with dictionary
    substitutions applied inside transcribe()) matches a snippet cue exactly,
    paste the expansion and skip LLM cleanup entirely."""
    snippet_expansion = expand_snippet(ctx.text_raw)
    if snippet_expansion is None:
        return None

    ms_total_snippet = int((time.monotonic() - ctx.t_start) * 1000)
    _append_history(
        ctx,
        transcript_clean=snippet_expansion,
        ms_cleanup=0,
        ms_total=ms_total_snippet,
        fallback=False,
    )
    global _last_paste_len, _last_paste_ts
    paste_text(snippet_expansion)
    _last_paste_len = len(snippet_expansion)
    _last_paste_ts = time.monotonic()
    log.info(
        "Dispatched snippet expansion (skipped cleanup) %dms total",
        ms_total_snippet,
    )
    _log_latency(ctx, 0, ms_total_snippet)
    return _Outcome(kind="snippet")


def _stage_cleanup_and_paste(ctx: _DispatchContext) -> _Outcome:
    """LLM cleanup + paste. Always terminal (the pipeline's default outcome).

    Inline formatting splits at "new paragraph" / "new line" / "next line"
    phrases found anywhere in the utterance, cleans each segment separately,
    then rejoins with the recorded newline breaks. The RAW fallback badge is
    surfaced when cleanup fell back to the unedited transcript."""
    inline_splits = _split_inline_formatting(ctx.text_raw)

    if inline_splits is None:
        # Fast path: no inline formatting phrase present.  Single clean + paste,
        # exactly the original behaviour.
        text_clean, fallback = clean(
            ctx.text_raw, ctx.effective_mode, _cfg.groq_api_key,
            translate_to_english=ctx.translate_flag,
        )
        if not ctx.translate_flag:
            text_clean = apply_substitutions(text_clean)
        t_cleanup_end = time.monotonic()
        ms_cleanup = int((t_cleanup_end - ctx.t_transcribe_end) * 1000)
        ms_total = int((t_cleanup_end - ctx.t_start) * 1000)
        _append_history(
            ctx,
            transcript_clean=text_clean,
            ms_cleanup=ms_cleanup,
            ms_total=ms_total,
            fallback=fallback,
        )
        _paste_with_breaks(text_clean)
    else:
        # Inline formatting path: clean each non-empty segment, join with \n.
        cleaned_parts = []
        any_fallback = False
        for segment, _nl in inline_splits:
            if not segment:
                cleaned_parts.append(("", False))
                continue
            if ctx.effective_mode == "raw":
                cleaned_parts.append((segment, False))
            else:
                seg_clean, seg_fallback = clean(
                    segment, ctx.effective_mode, _cfg.groq_api_key,
                    translate_to_english=ctx.translate_flag,
                )
                if not ctx.translate_flag:
                    seg_clean = apply_substitutions(seg_clean)
                cleaned_parts.append((seg_clean, seg_fallback))
                if seg_fallback:
                    any_fallback = True

        # Assemble final text with real newline separators.
        assembled_parts = []
        for (seg_clean, _), (_, nl) in zip(cleaned_parts, inline_splits):
            assembled_parts.append(seg_clean)
            if nl:
                assembled_parts.append("\n" * nl)
        text_clean = "".join(assembled_parts)

        t_cleanup_end = time.monotonic()
        ms_cleanup = int((t_cleanup_end - ctx.t_transcribe_end) * 1000)
        ms_total = int((t_cleanup_end - ctx.t_start) * 1000)
        _append_history(
            ctx,
            transcript_clean=text_clean,
            ms_cleanup=ms_cleanup,
            ms_total=ms_total,
            fallback=any_fallback,
        )
        _paste_with_breaks(text_clean)
        fallback = any_fallback

    log.info(
        "Dispatched [%s] lang=%s %dms total%s",
        ctx.effective_mode,
        ctx.language,
        ms_total,
        " (fallback)" if fallback else "",
    )
    _log_latency(ctx, ms_cleanup, ms_total)
    _recent_latency_ms.append(ms_total)
    return _Outcome(kind="cleaned_paste", fallback_badge=fallback)


# Cleanup-fallback toast throttle: session mode can produce a burst of RAW
# fallbacks in a row (for example when Groq is degraded); one toast per ten
# minutes is informative without being spam.
_FALLBACK_TOAST_INTERVAL_S = 600.0
_last_fallback_toast_ts: float = 0.0

# Degraded-cleanup escalation. The Aug 2026 model retirement ran ~30 hours
# of silent RAW fallbacks because a throttled toast and a transient badge
# were the only signals. At _DEGRADED_THRESHOLD consecutive cleanup
# fallbacks the overlay badge latches persistent-amber (click shows the
# cause) and one un-throttled toast fires. A single successful cleanup
# clears the state; short-circuit outcomes (snippets, voice commands) say
# nothing about cleanup health, so they leave the counter untouched.
_DEGRADED_THRESHOLD = 3
_consecutive_fallbacks = 0
_degraded_announced = False

# Offline-transcription toast throttle: separate timestamp from the cleanup
# fallback above, same one-per-ten-minutes reasoning (a run of network drops
# would otherwise spam a toast per burst).
_OFFLINE_TOAST_INTERVAL_S = 600.0
_last_offline_toast_ts: float = 0.0


def _finalise(outcome: _Outcome) -> None:
    """Single shared exit path for every terminal outcome.

    Resets the tray to idle and the overlay back to its post-dispatch state
    exactly once, in one place, so the reset can never drift between branches.
    The RAW badge is set from the outcome: the cleanup branch passes the real
    fallback flag, every short-circuit branch passes False. See the LATENT BUG
    note below."""
    global _last_fallback_toast_ts, _consecutive_fallbacks, _degraded_announced

    # Track cleanup health on genuine cleanup outcomes only.
    if outcome.kind == "cleaned_paste":
        if outcome.fallback_badge:
            _consecutive_fallbacks += 1
        else:
            _consecutive_fallbacks = 0
            _degraded_announced = False

    degraded = _consecutive_fallbacks >= _DEGRADED_THRESHOLD

    if _tray:
        _tray.set_idle()
        if outcome.tray_notify:
            _tray.notify(outcome.tray_notify, important=True)
        elif degraded and not _degraded_announced:
            # Escalation bypasses the 10-minute throttle exactly once per
            # degradation episode: repeated failure is news, spam is not.
            _degraded_announced = True
            _tray.notify(
                f"Cleanup has failed {_consecutive_fallbacks} times in a row. "
                "Click the amber RAW badge on the gadget for the reason.",
                important=True,
            )
        elif outcome.fallback_badge:
            now = time.monotonic()
            if now - _last_fallback_toast_ts >= _FALLBACK_TOAST_INTERVAL_S:
                _last_fallback_toast_ts = now
                _tray.notify("Cleanup fell back to raw transcript", important=True)
    if _overlay:
        # LATENT BUG FIX (flagged to the orchestrator): the pre-refactor code
        # only ever called set_fallback() on the cleanup branch, so a stale
        # RAW badge from a fallback paste survived across a following snippet,
        # voice command, capture command, empty burst or transcription error.
        # overlay.set_fallback's own docstring promises the badge clears "on
        # the next successful dispatch". Routing the badge through the single
        # finalise, with every short-circuit outcome carrying fallback_badge
        # False, honours that contract. The cleanup branch is unchanged.
        _overlay.set_fallback(outcome.fallback_badge)
        _overlay.set_degraded(cleanup_last_error() if degraded else None)
        _overlay.set_state(_post_dispatch_state())


def _dispatch(wav_path: Path) -> None:
    """Public dispatch entry point (signature and threading unchanged).

    Called from the hotkey thread (hold-to-talk release) and, one burst at a
    time, from the session worker thread. Runs the pipeline stages in order;
    the first stage to return an _Outcome wins and the rest are skipped. The
    cleanup stage always returns an outcome, so _finalise runs exactly once."""
    ctx = _DispatchContext(wav_path=wav_path, t_start=time.monotonic())

    if _tray:
        _tray.set_processing()
    if _overlay:
        _overlay.set_state("processing")

    _resolve_context(ctx)

    outcome = _stage_transcribe(ctx)
    if outcome is None:
        outcome = _stage_capture_command(ctx)
    if outcome is None:
        outcome = _stage_edit_command(ctx)
    if outcome is None:
        outcome = _stage_voice_command(ctx)
    if outcome is None:
        outcome = _stage_snippet(ctx)
    if outcome is None:
        outcome = _stage_cleanup_and_paste(ctx)

    _finalise(outcome)


# ---------------------------------------------------------------------------
# pynput backend (EXPERIMENTAL)
# ---------------------------------------------------------------------------
# Uses pynput.keyboard.Listener (press/release events) to implement hold-to-talk
# for Alt+1 without requiring admin rights.
#
# Confidence note: pynput's GlobalHotKeys uses RegisterHotKey which does NOT
# fire when an elevated window (Task Manager, UAC dialog) is in the
# foreground, but covers 95%+ of real dictation targets (browsers, email
# clients, word processors).  The Listener approach used here is slightly
# lower-level: it installs a WH_KEYBOARD_LL hook via pynput's Win32 backend,
# which DOES require elevation for the same elevated-window case, but is
# more reliable for hold detection than GlobalHotKeys (which only fires on
# press, not release).
#
# In practice:
# - "keyboard" backend: reliable everywhere, requires admin.
# - "pynput" backend: works in most apps without admin; misses elevated
#   windows.  Marked EXPERIMENTAL until broader test coverage.

def _wire_pynput_backend() -> None:
    """Register pynput Listener for Alt+1 hold-to-talk. EXPERIMENTAL.

    Known defect: pynput's plain keyboard.Listener has no suppression hook
    on this path (that requires a win32_event_filter callback, not wired
    here), so _on_alt1_press/_on_alt1_release's return values are ignored
    and every hotkey char keystroke reaches the focused app for the whole
    hold, e.g. "1111..." typed into Telegram's message box while dictating.
    This backend is inactive by default (input_backend="keyboard"); do not
    switch to it until real suppression is implemented.
    """
    global _PYNPUT_ACTIVE, _pynput_listener
    try:
        from pynput import keyboard as _pynput_kb
    except ImportError:
        log.error(
            "input_backend='pynput' is set but pynput is not installed. "
            "Run: pip install pynput  (or see requirements-optional.txt). "
            "Falling back to the keyboard library."
        )
        keyboard.on_press_key(settings.hotkey, _on_alt1_press, suppress=True)
        keyboard.on_release_key(settings.hotkey, _on_alt1_release, suppress=True)
        return

    # Map the hotkey string to a pynput Key. Currently only Alt+1 is
    # supported via this path (matching the default hotkey = "1",
    # hotkey_modifier = "alt").
    _hotkey_char = settings.hotkey          # "1"
    _modifier    = settings.hotkey_modifier  # "alt"

    _ALT_KEYS = {_pynput_kb.Key.alt, _pynput_kb.Key.alt_l, _pynput_kb.Key.alt_r}
    _pressed_keys: set = set()

    def _pynput_on_press(key) -> None:
        _pressed_keys.add(key)
        # Fire hold-to-talk start when the hotkey char is pressed AND the
        # modifier is already held.
        if _modifier == "alt":
            modifier_held = bool(_pressed_keys & _ALT_KEYS)
        else:
            modifier_held = False
        try:
            char = key.char
        except AttributeError:
            char = None
        if modifier_held and char == _hotkey_char:
            # Synthesise a minimal event object compatible with _on_alt1_press.
            class _Ev:
                name = _hotkey_char
                scan_code = 0
            _on_alt1_press(_Ev())

    def _pynput_on_release(key) -> None:
        _pressed_keys.discard(key)
        try:
            char = key.char
        except AttributeError:
            char = None
        if char == _hotkey_char:
            class _Ev:
                name = _hotkey_char
                scan_code = 0
            _on_alt1_release(_Ev())

    _pynput_listener = _pynput_kb.Listener(
        on_press=_pynput_on_press,
        on_release=_pynput_on_release,
    )
    _pynput_listener.start()
    _PYNPUT_ACTIVE = True
    log.info(
        "pynput backend active (EXPERIMENTAL). Hold Alt+%s to talk. "
        "Note: does not fire in elevated windows (Task Manager, UAC dialogs).",
        _hotkey_char,
    )
    log.warning(
        "pynput backend cannot suppress the hotkey char: Alt+%s will type "
        "'%s' repeatedly into the focused app for the duration of the hold. "
        "Use input_backend='keyboard' (the default) unless you accept that.",
        _hotkey_char, _hotkey_char,
    )


def _on_alt1_press(event) -> bool:
    """Return True to let the raw keystroke through, False to swallow it.

    Only suppresses the hotkey char while the modifier is held (the actual
    Alt+<hotkey> combo); a bare press of the char is left untouched so it
    still types normally everywhere, e.g. in Telegram's message box.

    Windows re-fires KEY_DOWN for a held key (key-repeat, ~30/s) while it
    stays down. Once a hold is claimed below, every repeat is suppressed
    immediately without re-testing is_pressed(modifier): that per-repeat
    re-test used to race Alt's key-up (Alt can read released a beat before
    the char physically is), so the tail of a long hold leaked raw "1"
    characters into the focused app (e.g. Telegram's message box). The
    claim only clears on release (_on_alt1_release), so a bare, un-modified
    tap of the char still types normally afterwards.
    """
    global _recording_active, _press_start_time, _hotkey_claimed
    if _hotkey_claimed:
        # Repeat KEY_DOWN of an already-claimed hold: keep suppressing.
        return False
    if not keyboard.is_pressed(settings.hotkey_modifier):
        return True
    _hotkey_claimed = True
    if _paused:
        return False
    if _session_active:
        # During session mode the hold-to-talk path is suppressed; only
        # the release-side double-tap detector fires to toggle the
        # session back off.
        _press_start_time = time.monotonic()
        return False
    _press_start_time = time.monotonic()
    with _recording_lock:
        if _recording_active:
            return False
        _recording_active = True
    log.debug("Recording started")
    if _tray:
        _tray.set_recording()
    if _overlay:
        _overlay.set_state("recording")
    _recorder.start()
    _arm_esc_cancel()
    return False


# --------------------------------------------------------------------------- #
# Mid-recording cancel (Esc)                                                   #
#                                                                              #
# The Esc hook exists ONLY while a hold-to-talk recording is in flight: armed  #
# right after recorder.start(), disarmed on normal release or on the cancel    #
# itself. Esc is never intercepted at any other time. Session-mode utterances  #
# are not cancellable this way (the VAD stream owns them); hold-to-talk only.  #
# --------------------------------------------------------------------------- #

_esc_hook = None  # keyboard hook handle, non-None only during a recording


def _arm_esc_cancel() -> None:
    global _esc_hook
    if _esc_hook is not None:
        return
    try:
        _esc_hook = keyboard.on_press_key("esc", _on_esc_cancel, suppress=True)
    except Exception as exc:
        log.warning("Esc cancel hook failed to arm: %s", exc)
        _esc_hook = None


def _disarm_esc_cancel() -> None:
    global _esc_hook
    if _esc_hook is None:
        return
    try:
        keyboard.unhook(_esc_hook)
    except Exception:
        pass
    _esc_hook = None


def _on_esc_cancel(event) -> bool:
    """Cancel the in-flight hold-to-talk recording: stop the recorder,
    delete the clip, dispatch nothing, return the UI to idle. Returns False
    (swallow Esc) when a recording was cancelled, True otherwise."""
    global _recording_active
    with _recording_lock:
        if not _recording_active:
            _disarm_esc_cancel()
            return True
        _recording_active = False
    _disarm_esc_cancel()
    try:
        wav = _recorder.stop()
        if wav is not None:
            wav.unlink(missing_ok=True)
    except Exception as exc:
        log.warning("Recorder stop on Esc cancel failed: %s", exc)
    log.info("Recording cancelled (Esc), clip discarded")
    if _tray:
        _tray.set_idle()
    if _overlay:
        _overlay.set_state("idle")
    return False


def _on_alt1_release(event) -> bool:
    global _recording_active, _last_tap_release_time, _hotkey_claimed
    if not _hotkey_claimed:
        return True
    _hotkey_claimed = False
    now = time.monotonic()
    press_duration_ms = (now - _press_start_time) * 1000 if _press_start_time else 9999

    # In-session: the only thing release does is watch for a double-tap
    # that toggles the session back off.
    if _session_active:
        if press_duration_ms < _SHORT_TAP_MAX_MS:
            time_since_last_tap_ms = (now - _last_tap_release_time) * 1000
            if time_since_last_tap_ms < _DOUBLE_TAP_WINDOW_MS:
                _last_tap_release_time = 0.0
                _on_session_toggle()
                return False
            _last_tap_release_time = now
        return False

    # Out-of-session: hold-to-talk path. Stop the recorder first so a
    # double-tap sequence doesn't keep the stream open.
    with _recording_lock:
        if not _recording_active:
            # No recording in flight. Still need to check double-tap to
            # enter session mode (the two short presses produce no actual
            # recording — recorder.start() ran but recorder.stop() returns
            # None for <250ms clips).
            if press_duration_ms < _SHORT_TAP_MAX_MS:
                time_since_last_tap_ms = (now - _last_tap_release_time) * 1000
                if time_since_last_tap_ms < _DOUBLE_TAP_WINDOW_MS:
                    _last_tap_release_time = 0.0
                    log.info("Double-tap detected, entering session mode")
                    _on_session_toggle()
                    return False
                _last_tap_release_time = now
            return False
        _recording_active = False
    _disarm_esc_cancel()
    log.debug("Recording stopped")
    wav = _recorder.stop()

    # Double-tap detection on a short tap that did record something tiny.
    if press_duration_ms < _SHORT_TAP_MAX_MS:
        time_since_last_tap_ms = (now - _last_tap_release_time) * 1000
        if time_since_last_tap_ms < _DOUBLE_TAP_WINDOW_MS:
            _last_tap_release_time = 0.0
            log.info("Double-tap detected, entering session mode")
            if _tray:
                _tray.set_idle()
            if _overlay:
                _overlay.set_state("idle")
            _on_session_toggle()
            return False
        _last_tap_release_time = now

    if wav is None:
        log.debug("Recording too short, discarded.")
        if _tray:
            _tray.set_idle()
        if _overlay:
            _overlay.set_state("idle")
        return False
    threading.Thread(target=_dispatch, args=(wav,), daemon=True).start()
    return False


_singleton_mutex = None  # named mutex kept alive for the whole process lifetime


def _ensure_single_instance() -> None:
    """Exit immediately if another FreeFlow instance is already running.

    Login-autostart launches FreeFlow at logon; if the user then also clicks
    the icon, a second copy would start and both would hook Alt+1, producing
    double pastes. A per-session named mutex makes the second launch detect the
    first and exit cleanly. If pywin32 is unavailable the guard is skipped
    rather than blocking startup.
    """
    global _singleton_mutex
    try:
        import win32event
        import win32api
        import winerror
    except Exception:
        return
    _singleton_mutex = win32event.CreateMutex(None, False, "FreeFlow-SingleInstance")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        log.warning("FreeFlow is already running; exiting this second instance.")
        sys.exit(0)


def main() -> None:
    _ensure_single_instance()
    if not _cfg.groq_api_key:
        print(
            "ERROR: GROQ_API_KEY is not set. Add it to .env at the repo root.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Back up user data files on startup if they exist and differ from the
    # newest backup. This runs before any editor is opened so no user action
    # is required to trigger a first backup on a fresh session.
    backup_if_changed(user_file("dictionary.json"))
    backup_if_changed(user_file("snippets.json"))

    _restore_persisted_state()

    _start_session_watchdog()

    try:
        from welcome import show_welcome_once
        show_welcome_once()
    except Exception as exc:
        log.warning("Welcome dialog skipped: %s", exc)

    global _tray, _overlay
    _tray = TrayIcon(
        on_pause_toggle=_on_pause_toggle,
        on_force_mode=_on_force_mode,
        on_show_last=_on_show_last,
        on_open_logs=_on_open_logs,
        on_quit=_on_quit,
        on_show_gadget=_on_show_gadget,
        on_edit_dictionary=_on_edit_dictionary,
        on_edit_snippets=_on_edit_snippets,
        on_about=_on_about,
        on_set_language=_set_dictation_language,
        get_dictation_language=lambda: settings.dictation_language,
        on_undo_paste=_undo_last_paste,
        on_meeting_toggle=_on_meeting_toggle,
        get_meeting_active=lambda: _meeting_active,
        on_compact_toggle=_on_compact_toggle,
        get_compact=lambda: _overlay.get_compact() if _overlay else False,
        get_recent=last_ten,
        on_copy_recent=_copy_recent_dictation,
        get_last_app=_last_dictated_app,
        on_route_last_app=_route_last_app,
    )
    _tray.start()

    _overlay = Overlay(
        on_pause_toggle=_on_pause_toggle,
        on_force_mode=_on_force_mode,
        on_set_translate=_on_set_translate,
        on_quit=_on_quit,
        get_auto_mode=_current_auto_mode,
        get_translate=lambda: _translate_to_english,
        get_forced=lambda: _forced_mode,
        get_detected_language=lambda: _detected_language,
        get_audio_level=lambda: _current_audio_level,
        on_hide_gadget=_on_hide_gadget,
        on_session_toggle=_on_session_toggle,
        get_session_active=lambda: _session_active,
        get_dictation_language=lambda: settings.dictation_language,
        on_cycle_language=_cycle_dictation_language,
        get_last_latency=lambda: (get_recent_latency_ms() or (None, None))[0],
        on_compact_toggle=_on_compact_toggle,
    )

    if settings.input_backend == "pynput":
        _wire_pynput_backend()
    else:
        # Default: keyboard library. suppress=True blocks the hotkey char at
        # the OS level while Alt is held, so it never leaks into the focused
        # app (e.g. "1" typed into Telegram); _on_alt1_press/_on_alt1_release
        # return False only for that combo, so a bare press of the char is
        # untouched. Admin rights required on Windows.
        keyboard.on_press_key(settings.hotkey, _on_alt1_press, suppress=True)
        keyboard.on_release_key(settings.hotkey, _on_alt1_release, suppress=True)

    # Startup self-check in a daemon thread: a dead or retired cleanup model
    # is discovered at launch, before the first dictation. A rejected primary
    # already switches to the fallback model inside cleanup; here we only
    # surface the news. Delayed a few seconds so the tray icon exists and a
    # slow network cannot hold up startup.
    def _cleanup_selfcheck() -> None:
        time.sleep(3.0)
        try:
            ok, detail = startup_selfcheck(_cfg.groq_api_key)
        except Exception as exc:  # never let the check take the app down
            log.warning("Startup self-check crashed: %s", exc)
            return
        if not ok:
            log.error("Startup self-check: cleanup endpoint unreachable: %s", detail)
            if _tray:
                _tray.notify(
                    "Cleanup service unreachable at startup. Dictation will "
                    "paste raw transcripts until it recovers.",
                    important=True,
                )
        elif detail:
            log.error("Startup self-check: %s", detail)
            if _tray:
                _tray.notify(detail, important=True)
        else:
            # Logged so a healthy check is distinguishable from a check that
            # never ran: silence must never be the success signal.
            log.info("Startup cleanup self-check passed.")

    threading.Thread(target=_cleanup_selfcheck, daemon=True).start()

    log.info(
        "FreeFlow dictation running. Alt+1 = hold-to-talk. "
        "Mode + translate via the floating gadget."
    )
    # Overlay mainloop blocks the main thread; keyboard hooks fire from
    # their own internal thread so they remain live.
    _overlay.run()


if __name__ == "__main__":
    main()
