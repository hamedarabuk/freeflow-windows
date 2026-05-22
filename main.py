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
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import keyboard

from config import load_config
from cleanup import clean
from dictionary import apply_substitutions
from history import append, last_ten, log_dir
from paste import paste_text
from recorder import Recorder
from router import pick_mode, _get_foreground_info
from snippets import expand_snippet
from transcribe import transcribe
from tray import TrayIcon
from overlay import Overlay, load_state as _load_overlay_state

# Session mode (VAD-driven continuous capture). Imported lazily inside
# the toggle handler so a missing webrtcvad install doesn't block startup
# of the regular hold-to-talk path.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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

# Live audio level (0..~1), written by the audio thread, read by the overlay
# poll loop. Single-writer / single-reader, atomic in CPython so no lock needed.
_current_audio_level: float = 0.0


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
_DOUBLE_TAP_WINDOW_MS = 400
_SHORT_TAP_MAX_MS = 250

# Session-mode burst dispatch queue. Bursts arrive from the VAD audio thread
# and must be processed strictly in order: parallel dispatches race on the
# shared clipboard (paste_text uses pyperclip.copy + keyboard.send Ctrl+V,
# and a second copy clobbers the first before its paste fires, producing
# garbled output). A single worker thread drains the queue.
_session_dispatch_queue: "queue.Queue[Optional[Path]]" = queue.Queue()
_session_worker: Optional[threading.Thread] = None
_session_worker_stop = threading.Event()


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


def _on_show_last() -> None:
    records = last_ten()
    if not records:
        if _tray:
            _tray.notify("No dictation history today.")
        return
    path = log_dir() / f"{time.strftime('%Y-%m-%d')}.jsonl"
    os.startfile(str(path))


def _on_open_logs() -> None:
    os.startfile(str(log_dir()))


def _ensure_user_config(name: str) -> Path:
    """Return <name>.json from the repo root. If missing, seed from .example."""
    here = Path(__file__).resolve().parent
    target = here / f"{name}.json"
    example = here / f"{name}.json.example"
    if not target.exists() and example.exists():
        try:
            target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("Seeded %s from example", target.name)
        except Exception as exc:
            log.warning("Failed to seed %s: %s", target.name, exc)
    return target


def _on_edit_dictionary() -> None:
    os.startfile(str(_ensure_user_config("dictionary")))


def _on_edit_snippets() -> None:
    os.startfile(str(_ensure_user_config("snippets")))


def _on_quit() -> None:
    log.info("Quit requested.")
    if _overlay:
        _overlay.stop()
    if _tray:
        _tray.stop()
    keyboard.unhook_all()
    sys.exit(0)


def _on_hide_gadget() -> None:
    log.info("Gadget hidden (tray still running; click tray icon to restore).")
    if _overlay:
        _overlay.hide()
    if _tray:
        _tray.notify("Gadget hidden. Click the tray icon to restore.")


def _on_show_gadget() -> None:
    log.info("Showing gadget.")
    if _overlay:
        _overlay.show()


def _on_session_burst(wav_path: Path) -> None:
    """SessionManager calls this from the audio thread for each finalised
    speech burst. Enqueue and return immediately; a single worker thread
    drains the queue so dispatches stay strictly serial and the audio
    callback is never blocked."""
    _session_dispatch_queue.put(wav_path)


def _session_worker_loop() -> None:
    """Drain the session dispatch queue strictly in order. Sentinel value
    None tells the worker to exit."""
    while not _session_worker_stop.is_set():
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
                _tray.notify("Session needs webrtcvad-wheels. Install + restart.")
            return
        if not is_available():
            if _tray:
                _tray.notify("webrtcvad-wheels not installed. pip install webrtcvad-wheels")
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
                _tray.notify(f"Session start failed: {exc}")


def _dispatch(wav_path: Path) -> None:
    global _detected_language
    t_start = time.monotonic()

    if _tray:
        _tray.set_processing()
    if _overlay:
        _overlay.set_state("processing")

    process_name, window_title = _get_foreground_info()
    mode_auto = pick_mode(process_name, window_title)

    # Persistent override (v2): do NOT clear _forced_mode after the dispatch.
    with _forced_mode_lock:
        mode_forced = _forced_mode

    effective_mode = mode_forced if mode_forced else mode_auto

    with _translate_lock:
        translate_flag = _translate_to_english

    t_rec_end = time.monotonic()
    ms_record = int((t_rec_end - t_start) * 1000)

    try:
        text_raw, language = transcribe(wav_path, _cfg.groq_api_key)
    except Exception as exc:
        log.error("Transcription failed: %s", exc)
        if _tray:
            _tray.set_idle()
            _tray.notify("Transcription failed.")
        if _overlay:
            _overlay.set_state("idle")
        return
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass

    # Track detected language for the overlay's language pill.
    _detected_language = (language or "")[:2].lower()

    t_transcribe_end = time.monotonic()
    ms_transcribe = int((t_transcribe_end - t_rec_end) * 1000)

    # Empty transcript means transcribe() dropped a silent or hallucinated
    # burst. Skip the cleanup + paste round-trip and return to idle.
    if not text_raw.strip():
        log.info(
            "Skipped dispatch: empty transcript (silence/noise/hallucination filter)"
        )
        if _tray:
            _tray.set_idle()
        if _overlay:
            _overlay.set_state("idle")
        return

    # Snippet shortcut: if the transcribed text (already with dictionary
    # substitutions applied inside transcribe()) matches a snippet cue
    # exactly, paste the expansion and skip LLM cleanup entirely.
    snippet_expansion = expand_snippet(text_raw)
    if snippet_expansion is not None:
        ms_total_snippet = int((time.monotonic() - t_start) * 1000)
        try:
            append(
                mode_auto=mode_auto,
                mode_forced=mode_forced,
                language=language,
                transcript_raw=text_raw,
                transcript_clean=snippet_expansion,
                app_process=process_name,
                app_title=window_title,
                ms_record=ms_record,
                ms_transcribe=ms_transcribe,
                ms_cleanup=0,
                ms_total=ms_total_snippet,
                fallback=False,
            )
        except Exception as exc:
            log.warning("History append failed: %s", exc)
        paste_text(snippet_expansion)
        log.info(
            "Dispatched snippet expansion (skipped cleanup) %dms total",
            ms_total_snippet,
        )
        if _tray:
            _tray.set_idle()
        if _overlay:
            _overlay.set_state("idle")
        return

    text_clean, fallback = clean(
        text_raw, effective_mode, _cfg.groq_api_key,
        translate_to_english=translate_flag,
    )

    # Re-apply dictionary substitutions to undo any paraphrasing by the
    # cleanup LLM. transcribe() applies them once before cleanup; this
    # second pass locks them in after cleanup. Skipped when translating
    # because the cleanup output is in a different language to the
    # dictionary keys.
    if not translate_flag:
        text_clean = apply_substitutions(text_clean)

    t_cleanup_end = time.monotonic()
    ms_cleanup = int((t_cleanup_end - t_transcribe_end) * 1000)
    ms_total = int((t_cleanup_end - t_start) * 1000)

    try:
        append(
            mode_auto=mode_auto,
            mode_forced=mode_forced,
            language=language,
            transcript_raw=text_raw,
            transcript_clean=text_clean,
            app_process=process_name,
            app_title=window_title,
            ms_record=ms_record,
            ms_transcribe=ms_transcribe,
            ms_cleanup=ms_cleanup,
            ms_total=ms_total,
            fallback=fallback,
        )
    except Exception as exc:
        log.warning("History append failed: %s", exc)

    paste_text(text_clean)
    log.info(
        "Dispatched [%s] lang=%s %dms total%s",
        effective_mode,
        language,
        ms_total,
        " (fallback)" if fallback else "",
    )
    if _tray:
        _tray.set_idle()
    if _overlay:
        _overlay.set_state("idle")


def _on_alt1_press(event) -> None:
    global _recording_active, _press_start_time
    if _paused:
        return
    if not keyboard.is_pressed("alt"):
        return
    if _session_active:
        # During session mode the hold-to-talk path is suppressed; only
        # the release-side double-tap detector fires to toggle the
        # session back off.
        _press_start_time = time.monotonic()
        return
    _press_start_time = time.monotonic()
    with _recording_lock:
        if _recording_active:
            return
        _recording_active = True
    log.debug("Recording started")
    if _tray:
        _tray.set_recording()
    if _overlay:
        _overlay.set_state("recording")
    _recorder.start()


def _on_alt1_release(event) -> None:
    global _recording_active, _last_tap_release_time
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
                return
            _last_tap_release_time = now
        return

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
                    return
                _last_tap_release_time = now
            return
        _recording_active = False
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
            return
        _last_tap_release_time = now

    if wav is None:
        log.debug("Recording too short, discarded.")
        if _tray:
            _tray.set_idle()
        if _overlay:
            _overlay.set_state("idle")
        return
    threading.Thread(target=_dispatch, args=(wav,), daemon=True).start()


def main() -> None:
    if not _cfg.groq_api_key:
        print(
            "ERROR: GROQ_API_KEY is not set. Add it to .env at the repo root.",
            file=sys.stderr,
        )
        sys.exit(1)

    _restore_persisted_state()

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
    )

    keyboard.on_press_key("1", _on_alt1_press, suppress=False)
    keyboard.on_release_key("1", _on_alt1_release, suppress=False)

    log.info(
        "FreeFlow dictation running. Alt+1 = hold-to-talk. "
        "Mode + translate via the floating gadget."
    )
    # Overlay mainloop blocks the main thread; keyboard hooks fire from
    # their own internal thread so they remain live.
    _overlay.run()


if __name__ == "__main__":
    main()
