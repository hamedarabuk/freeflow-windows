"""
overlay.py — floating CTk gadget for the dictation service (v2).

Whispr Flow-style widget with:
  - state LED (idle / recording / processing / paused)
  - language pill (last detected input language: EN, FA, ...)
  - clickable mode pill that opens a dropdown menu
    (Auto, per-mode override, Translate-to-English toggle, Quit)
  - persistent forced-mode override (was one-shot in v1)
  - translate-to-British-English toggle that survives restarts

Persists position + forced_mode + translate flag to .overlay-state.json.
Runs on the main thread via the CTk mainloop (called from main.py via run()).
"""

from __future__ import annotations

import json
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Optional

import customtkinter as ctk

from paths import logs_dir

ctk.set_appearance_mode("dark")

MODES = ["polished", "brand_voice", "prompt", "note", "raw"]

# --- Colour palette ---
BG_ROOT          = "#1e1e1e"
BG_PILL          = "#2a2a2a"
BG_PILL_HOVER    = "#363636"
BG_MENU          = "#252525"
BG_MENU_HOVER    = "#374151"
BG_MENU_ACTIVE   = "#1f3a5f"
BG_LANG_PILL     = "#374151"
BG_TRANSLATE_ON  = "#1e3a8a"
SEP_COLOUR       = "#3a3a3a"

# Pause button: filled blue-family chip, deliberately distinct from the
# mic button's outlined red-family circle (see the mic/pause differentiation
# note in _refresh and _build).
BG_PAUSE_IDLE    = "#1f2937"
FG_PAUSE_IDLE    = "#93c5fd"
BG_PAUSE_ACTIVE  = "#1d4ed8"
FG_PAUSE_ACTIVE  = "#dbeafe"

FG_PRIMARY       = "#f1f5f9"
FG_SECONDARY     = "#9ca3af"
FG_MUTED         = "#6b7280"
FG_LANG          = "#e5e7eb"
FG_TRANSLATE     = "#dbeafe"

STATE_COLOURS = {
    "idle":       "#6b7280",
    "recording":  "#ef4444",
    "processing": "#f59e0b",
    "paused":     "#6b7280",
    "session":    "#8b5cf6",
    "meeting":    "#0ea5e9",
}

STATE_LABELS = {
    "idle":       "Idle",
    "recording":  "Listening...",
    "processing": "Processing...",
    "paused":     "Paused",
    "session":    "Listening (session)",
    "meeting":    "Meeting notes",
}

FONT_FAMILY = "Segoe UI Variable"

_STATE_FILE = Path(__file__).parent / ".overlay-state.json"

_W = 300
_H = 132       # includes the 14px grip bar at the top

# Fixed width for the state label whilst it carries a live "processing Ns"
# elapsed-time suffix, so the extra digits don't reflow the row (and the
# lang pill next to it) on every tick. Reverts to auto-width (0) otherwise.
_PROCESSING_LABEL_WIDTH = 150

# How long the post-dispatch latency suffix (e.g. "Idle 1.8s") stays on the
# state label after returning to rest, before reverting to the plain label.
_LATENCY_SUFFIX_S = 3.0
_GRIP_H = 14
_MARGIN_RIGHT  = 24
_MARGIN_BOTTOM = 48

# Mic toggle: round button, prominent in the bottom row.
_MIC_SIZE = 36     # square frame, corner_radius=half makes it a circle
_MIC_GLYPH_FONT = 18

# Mode pill: compact (the mode menu is read once per app, not interacted with often).
_MODE_PILL_H = 30
_MODE_PILL_FONT = 12

# Equaliser
_EQ_BARS = 7
_EQ_BAR_W = 4
_EQ_BAR_GAP = 2
_EQ_W = _EQ_BARS * _EQ_BAR_W + (_EQ_BARS - 1) * _EQ_BAR_GAP  # 40
_EQ_H = 14
_EQ_GAIN = 220    # multiplies RMS to map speech (~0.05-0.15) to bar heights
_EQ_TICK_MS = 50  # 20 Hz redraw

# Compact mode: how long after the pointer leaves the gadget it stays
# hover-expanded before auto-collapsing back down.
_HOVER_COLLAPSE_DELAY_MS = 2500


# ---------------------------------------------------------------------------
# Persisted state (single writer: overlay)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


_MODE_DESCRIPTIONS = {
    "polished":    "Fix grammar, filler, flow",
    "brand_voice": "Brand tone, short sentences",
    "prompt":      "Shape into AI instruction",
    "note":        "Light touch, keep fragments",
    "raw":         "Transcription errors only",
}

_MODE_MENU_LABELS = {
    "polished":    "Polished  (fix grammar + filler)",
    "brand_voice": "Brand voice  (short sentences, brand tone)",
    "prompt":      "Prompt  (shape into AI instruction)",
    "note":        "Note  (light touch, keep fragments)",
    "raw":         "Raw  (transcription errors only)",
}


def _format_mode_label(mode: str) -> str:
    return mode.replace("_", " ").upper()


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

class Overlay:
    def __init__(
        self,
        on_pause_toggle: Callable[[], None],
        on_force_mode: Callable[[Optional[str]], None],
        on_set_translate: Callable[[bool], None],
        on_quit: Callable[[], None],
        get_auto_mode: Callable[[], str],
        get_translate: Callable[[], bool],
        get_forced: Callable[[], Optional[str]],
        get_detected_language: Callable[[], str],
        get_audio_level: Callable[[], float],
        on_hide_gadget: Optional[Callable[[], None]] = None,
        on_session_toggle: Optional[Callable[[], None]] = None,
        get_session_active: Optional[Callable[[], bool]] = None,
        get_dictation_language: Optional[Callable[[], str]] = None,
        on_cycle_language: Optional[Callable[[], None]] = None,
        get_last_latency: Optional[Callable[[], Optional[int]]] = None,
        on_compact_toggle: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_pause_toggle = on_pause_toggle
        self._on_force_mode = on_force_mode
        self._on_set_translate = on_set_translate
        self._on_quit = on_quit
        self._on_hide_gadget = on_hide_gadget
        self._on_session_toggle = on_session_toggle
        self._get_session_active = get_session_active or (lambda: False)
        self._get_auto_mode = get_auto_mode
        self._get_translate = get_translate
        self._get_forced = get_forced
        self._get_detected_language = get_detected_language
        self._get_audio_level = get_audio_level
        # Language pill: shows and controls settings.dictation_language
        # (not the detected language above). Clicking cycles en -> fa -> auto.
        self._get_dictation_language = get_dictation_language or (lambda: "en")
        self._on_cycle_language = on_cycle_language
        # Last dispatch's ms_total, for the brief post-dispatch latency
        # suffix on the resting state label.
        self._get_last_latency = get_last_latency or (lambda: None)
        self._on_compact_toggle = on_compact_toggle

        # Compact mode: collapses the gadget to grip bar + LED + mic button,
        # hiding the mode pill, hint text, language pill, and badges. Hover
        # (or the pointer never having left) expands it back temporarily;
        # it auto-collapses again after _HOVER_COLLAPSE_DELAY_MS of the
        # pointer being away. Persisted alongside position in
        # .overlay-state.json (loaded in _build(), like x/y).
        self._compact = False
        self._hover_expanded = False
        self._hover_collapse_job: Optional[str] = None

        self._state = "idle"
        self._processing_started: Optional[float] = None
        self._latency_shown_at: Optional[float] = None
        self._paused = False
        self._last_fallback = False  # True when the last dispatch used the raw transcript
        self._menu_top: Optional[ctk.CTkToplevel] = None

        self._drag_start_x = 0
        self._drag_start_y = 0
        # Cursor offset from window origin at press time. Used by the
        # robust drag path (compute absolute position from cursor) which
        # works regardless of whether geometry() reports current position
        # accurately between motion events.
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._dragging = False

        # Equaliser state: rolling window of recent RMS values + Canvas refs.
        self._eq_history: Deque[float] = deque([0.0] * _EQ_BARS, maxlen=_EQ_BARS)
        self._eq_canvas: Optional[tk.Canvas] = None
        self._eq_bars: list = []
        self._eq_tick_scheduled = False

        self._root: Optional[ctk.CTk] = None

    # ------------------------------------------------------------------
    # Public API (thread-safe via root.after)
    # ------------------------------------------------------------------

    @property
    def tk_root(self) -> Optional[ctk.CTk]:
        """The live CTk root, or None before run() is called. Use its .after()
        to marshal work onto the main Tk loop from another thread."""
        return self._root

    def set_state(self, state: str) -> None:
        # Leaving processing: stamp the moment so the resting state label
        # can show a brief latency suffix (see _refresh).
        if self._state == "processing" and state != "processing":
            self._latency_shown_at = time.monotonic()
        self._state = state
        # Recorded synchronously (not deferred to the Tk thread) so the
        # elapsed clock starts the instant "processing" begins, whichever
        # thread called this. Cleared on leaving the state so a later
        # re-entry starts a fresh clock.
        self._processing_started = time.monotonic() if state == "processing" else None
        if self._root:
            self._root.after(0, self._refresh)

    def set_forced(self, mode: Optional[str]) -> None:
        if self._root:
            self._root.after(0, self._persist_forced_and_refresh, mode)

    def set_translate(self, on: bool) -> None:
        if self._root:
            self._root.after(0, self._persist_translate_and_refresh, on)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if self._root:
            self._root.after(0, self._refresh)

    def set_compact(self, on: bool) -> None:
        """Toggle compact mode. Shared by the mode-menu item and the tray
        menu item (both call the same main.py handler).

        The flag itself flips synchronously here (a single bool, atomic
        under the GIL) so a caller on another thread, e.g. the tray, can
        read get_compact() back immediately after this returns. The visual
        work (row hide/show, window resize, persistence) is marshalled onto
        the Tk thread, same convention as every other cross-thread setter
        on this class.
        """
        self._compact = bool(on)
        self._hover_expanded = False
        if self._root:
            self._root.after(0, self._apply_compact_and_persist)

    def get_compact(self) -> bool:
        return self._compact

    def set_fallback(self, fallback: bool) -> None:
        """Signal whether the last dispatch fell back to the raw transcript.

        When True, a small 'RAW' badge is shown in the bottom row so the user
        can see that cleanup was skipped (e.g. Groq timed out) and the pasted
        text is the unedited Whisper output.  Cleared automatically on the next
        successful dispatch.
        """
        self._last_fallback = fallback
        if self._root:
            self._root.after(0, self._refresh)

    def run(self) -> None:
        self._root = ctk.CTk()
        self._build()
        self._poll_mode()
        self._root.mainloop()

    def stop(self) -> None:
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def hide(self) -> None:
        """Hide the gadget window. Tray icon stays. Restore with show()."""
        if self._root:
            self._root.after(0, self._root.withdraw)

    def show(self) -> None:
        """Restore the gadget window after a hide()."""
        if self._root:
            try:
                self._root.after(0, self._root.deiconify)
                self._root.after(50, lambda: self._root.attributes("-topmost", True))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = self._root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.92)
        root.configure(fg_color=BG_ROOT)
        root.resizable(False, False)

        # Restore position
        state = load_state()
        sx = state.get("x")
        sy = state.get("y")
        self._compact = bool(state.get("compact", False))
        if sx is None or sy is None:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            sx = sw - _W - _MARGIN_RIGHT
            sy = sh - _H - _MARGIN_BOTTOM
        root.geometry(f"{_W}x{_H}+{int(sx)}+{int(sy)}")

        outer = ctk.CTkFrame(root, fg_color=BG_ROOT, corner_radius=12)
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        # --- Grip bar (drag handle) ---
        # Thin strip at the very top with a centred dot pattern. Full-width
        # draggable. Uses plain tk widgets because CTk paints its content on
        # an internal canvas that doesn't forward mouse events to outer
        # widget bindings — drag handlers wouldn't fire reliably.
        self._grip = tk.Frame(outer, bg="#262626", height=_GRIP_H, cursor="fleur")
        self._grip.pack(fill="x", padx=8, pady=(4, 0))
        self._grip.pack_propagate(False)
        self._grip_label = tk.Label(
            self._grip, text="• • • • •",
            bg="#262626", fg="#9ca3af",
            font=(FONT_FAMILY, 9, "bold"),
            cursor="fleur",
        )
        self._grip_label.pack(expand=True, fill="both")

        # --- Top row: LED + state label / equaliser + language pill ---
        top = ctk.CTkFrame(outer, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(4, 0))

        self._led = ctk.CTkFrame(
            top, width=10, height=10,
            fg_color=STATE_COLOURS["idle"], corner_radius=5,
        )
        self._led.pack(side="left", pady=(4, 0))
        self._led.pack_propagate(False)

        self._label_state = ctk.CTkLabel(
            top, text=STATE_LABELS["idle"],
            text_color=FG_SECONDARY,
            font=(FONT_FAMILY, 10),
        )
        self._label_state.pack(side="left", padx=(6, 0))

        # Equaliser canvas (hidden until recording). Sits in place of the
        # state label whilst the user is holding Alt+1.
        self._eq_canvas = tk.Canvas(
            top, width=_EQ_W, height=_EQ_H,
            bg=BG_ROOT, highlightthickness=0, bd=0,
        )
        for i in range(_EQ_BARS):
            x = i * (_EQ_BAR_W + _EQ_BAR_GAP)
            rect = self._eq_canvas.create_rectangle(
                x, _EQ_H - 2, x + _EQ_BAR_W, _EQ_H,
                fill="#ef4444", outline="",
            )
            self._eq_bars.append(rect)
        # not packed; _refresh manages visibility

        self._lang_pill = ctk.CTkLabel(
            top, text="EN",
            fg_color=BG_LANG_PILL, text_color=FG_LANG,
            font=(FONT_FAMILY, 9, "bold"),
            corner_radius=6,
            width=28, height=16,
        )  # packed/forgotten by _refresh
        # Clickable: shows the dictation-language lock (en/fa/auto) and
        # cycles it on click via self._on_cycle_language.
        self._lang_pill._canvas.configure(cursor="hand2")
        self._lang_pill.bind("<Button-1>", self._on_lang_pill_clicked)
        self._lang_pill._canvas.bind("<Button-1>", self._on_lang_pill_clicked)

        # --- Middle row: mode pill button ---
        # Compact: the mode menu is configuration the user reads occasionally,
        # not the primary interaction. The mic button gets the visual weight.
        self._mode_button = ctk.CTkButton(
            outer,
            text="AUTO · POLISHED  ▾",
            fg_color=BG_PILL, hover_color=BG_PILL_HOVER,
            text_color=FG_PRIMARY,
            font=(FONT_FAMILY, _MODE_PILL_FONT, "bold"),
            corner_radius=8,
            height=_MODE_PILL_H,
            command=self._toggle_menu,
        )
        self._mode_button.pack(fill="x", padx=10, pady=(6, 0))

        # --- Bottom row: hint + translate badge + pause icon ---
        bottom = ctk.CTkFrame(outer, fg_color="transparent")
        bottom.pack(fill="x", padx=10, pady=(4, 6))

        self._label_hint = ctk.CTkLabel(
            bottom, text="Alt + 1 to talk",
            text_color=FG_MUTED, font=(FONT_FAMILY, 9),
        )
        self._label_hint.pack(side="left")

        self._pause_button = ctk.CTkButton(
            bottom, text="⏸",
            fg_color=BG_PAUSE_IDLE, hover_color=BG_MENU_HOVER,
            text_color=FG_PAUSE_IDLE,
            font=(FONT_FAMILY, 11),
            width=22, height=22,
            corner_radius=6,
            command=self._on_pause_clicked,
        )
        self._pause_button.pack(side="right")

        # Mic toggle: one-click entry/exit into session mode (no Alt+1
        # double-tap needed). Round button (corner_radius = half the
        # height makes a perfect circle). Idle = grey outline; active =
        # red filled disc. Sits left of the pause icon and is the
        # largest interactive control on the gadget.
        self._mic_button = ctk.CTkButton(
            bottom, text="◉",
            fg_color="transparent", hover_color=BG_MENU_HOVER,
            text_color=FG_SECONDARY,
            font=(FONT_FAMILY, _MIC_GLYPH_FONT, "bold"),
            width=_MIC_SIZE, height=_MIC_SIZE,
            corner_radius=_MIC_SIZE // 2,
            border_width=2,
            border_color=FG_SECONDARY,
            command=self._on_mic_clicked,
        )
        self._mic_button.pack(side="right", padx=(0, 6))

        self._translate_badge = ctk.CTkLabel(
            bottom, text="⇄ Translate ON",
            fg_color=BG_TRANSLATE_ON, text_color=FG_TRANSLATE,
            font=(FONT_FAMILY, 9, "bold"),
            corner_radius=6,
            width=90, height=16,
        )  # packed/forgotten by _refresh

        # Fallback badge: shown briefly after a dispatch that fell back to
        # the raw Whisper transcript (cleanup timed out or was rejected).
        # Gives the user confidence that the pasted text is unedited audio
        # output, not a hallucinated LLM rewrite.
        self._fallback_badge = ctk.CTkLabel(
            bottom, text="RAW",
            fg_color="#7c3aed", text_color="#ede9fe",
            font=(FONT_FAMILY, 9, "bold"),
            corner_radius=6,
            width=32, height=16,
        )  # packed/forgotten by _refresh

        # Drag on the grip bar (primary) + other non-interactive children
        # (NOT on the mode pill, pause icon, or translate badge).
        # CTk widgets paint on an internal canvas that doesn't propagate
        # mouse events to the outer widget's bindings, so we must bind on
        # widget._canvas as well as the widget itself. _bind_drag handles
        # both cases.
        drag_targets = (
            self._grip, self._grip_label,
            root, outer, top, bottom,
            self._label_state, self._label_hint, self._led,
        )
        for widget in drag_targets:
            self._bind_drag(widget)

        # Compact mode: hover anywhere on the gadget expands it; the same
        # widgets as the drag targets above, since a hover boundary check
        # only needs a widget to attach to, not the drag-specific canvas
        # quirk (Enter/Leave fire on plain CTk/tk widgets directly).
        hover_targets = (
            root, outer, self._grip, self._grip_label, top, bottom,
            self._led, self._label_state, self._mic_button, self._pause_button,
        )
        for widget in hover_targets:
            self._bind_hover(widget)

        self._refresh()
        if self._compact:
            self._apply_compact_height()

    # ------------------------------------------------------------------
    # State refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._root is None:
            return

        # State row + equaliser swap. Show the equaliser whenever the
        # mic is live (hold-to-talk recording OR session mode listening).
        is_recording = (self._state in ("recording", "session") and not self._paused)
        if self._paused:
            self._led.configure(fg_color=STATE_COLOURS["paused"])
            self._label_state.configure(text="Paused")
        else:
            colour = STATE_COLOURS.get(self._state, STATE_COLOURS["idle"])
            self._led.configure(fg_color=colour)
            label_text = STATE_LABELS.get(self._state, "Idle")
            # Live elapsed-time suffix whilst processing, so a slow cloud
            # round-trip doesn't look frozen. Only once the wait has run
            # past 1s, to avoid flicker on fast (sub-1s) round-trips.
            if self._state == "processing" and self._processing_started is not None:
                elapsed = time.monotonic() - self._processing_started
                if elapsed >= 1.0:
                    label_text = f"{label_text} {elapsed:.1f}s"
                    self._label_state.configure(width=_PROCESSING_LABEL_WIDTH, anchor="w")
                else:
                    self._label_state.configure(width=0)
            elif (
                self._latency_shown_at is not None
                and time.monotonic() - self._latency_shown_at < _LATENCY_SUFFIX_S
            ):
                # Brief post-dispatch latency suffix on the resting state
                # label, same pattern as the processing elapsed-time suffix
                # above: reverts on a later _refresh tick once the window
                # passes, no dedicated timer.
                last_ms = self._get_last_latency()
                if last_ms is not None:
                    label_text = f"{label_text} {last_ms / 1000:.1f}s"
                    self._label_state.configure(width=_PROCESSING_LABEL_WIDTH, anchor="w")
                else:
                    self._label_state.configure(width=0)
            else:
                self._label_state.configure(width=0)
                self._latency_shown_at = None
            self._label_state.configure(text=label_text)

        # Show equaliser whilst recording, otherwise show the state label.
        if self._eq_canvas is not None:
            if is_recording:
                if self._label_state.winfo_ismapped():
                    self._label_state.pack_forget()
                if not self._eq_canvas.winfo_ismapped():
                    self._eq_canvas.pack(side="left", padx=(6, 0), pady=(2, 0))
                if not self._eq_tick_scheduled:
                    self._eq_tick_scheduled = True
                    self._root.after(_EQ_TICK_MS, self._eq_tick)
            else:
                if self._eq_canvas.winfo_ismapped():
                    self._eq_canvas.pack_forget()
                if not self._label_state.winfo_ismapped():
                    self._label_state.pack(side="left", padx=(6, 0))

        # Compact mode: hides the mode pill, hint text, language pill, and
        # badges (see set_compact's docstring). Hover-expand shows them all
        # again temporarily without leaving compact mode.
        expanded = (not self._compact) or self._hover_expanded

        # Mode pill
        if expanded:
            if not self._mode_button.winfo_ismapped():
                self._mode_button.pack(fill="x", padx=10, pady=(6, 0))
            if self._paused:
                self._mode_button.configure(text="PAUSED  ▾")
            else:
                forced = self._get_forced()
                if forced:
                    self._mode_button.configure(text=f"{_format_mode_label(forced)}  ▾")
                else:
                    auto = self._get_auto_mode()
                    self._mode_button.configure(text=f"AUTO · {_format_mode_label(auto)}  ▾")
        elif self._mode_button.winfo_ismapped():
            self._mode_button.pack_forget()

        # Hint text ("Alt + 1 to talk"): same compact/expanded gate.
        if expanded:
            if not self._label_hint.winfo_ismapped():
                self._label_hint.pack(side="left")
        elif self._label_hint.winfo_ismapped():
            self._label_hint.pack_forget()

        # Language pill: shows the dictation-language LOCK, not the detected
        # language. Always visible (it is a control, not transient
        # detection). "auto" shows the detected language alongside the lock
        # when one is available, e.g. "AUTO:EN".
        lock = self._get_dictation_language()
        lock_upper = (lock or "auto").upper()
        if lock_upper == "AUTO":
            detected = self._get_detected_language()
            pill_text = f"AUTO:{detected.upper()}" if detected else "AUTO"
        else:
            pill_text = lock_upper
        self._lang_pill.configure(text=pill_text)
        if expanded:
            if not self._lang_pill.winfo_ismapped():
                self._lang_pill.pack(side="right", pady=(2, 0))
        elif self._lang_pill.winfo_ismapped():
            self._lang_pill.pack_forget()

        # Translate badge (compact mode hides it regardless of the flag).
        if expanded and self._get_translate():
            if not self._translate_badge.winfo_ismapped():
                self._translate_badge.pack(side="right", padx=(0, 6))
        else:
            if self._translate_badge.winfo_ismapped():
                self._translate_badge.pack_forget()

        # Fallback badge: visible when the last dispatch used the raw
        # transcript (compact mode hides it regardless).
        if expanded and self._last_fallback:
            if not self._fallback_badge.winfo_ismapped():
                self._fallback_badge.pack(side="right", padx=(0, 4))
        else:
            if self._fallback_badge.winfo_ismapped():
                self._fallback_badge.pack_forget()

        # Pause glyph + colour. A filled rounded-square chip in a cool blue
        # family, distinct from the mic button's outlined red-family circle
        # beside it, so the two adjacent, similarly sized controls cannot be
        # mistaken for one another at a glance.
        if self._paused:
            self._pause_button.configure(
                text="▶", fg_color=BG_PAUSE_ACTIVE, text_color=FG_PAUSE_ACTIVE,
            )
        else:
            self._pause_button.configure(
                text="⏸", fg_color=BG_PAUSE_IDLE, text_color=FG_PAUSE_IDLE,
            )

        # Mic button: shows the current session state. Active = solid
        # red disc with no border; idle = grey outlined ring (visible
        # against the dark gadget background).
        session_on = False
        try:
            session_on = bool(self._get_session_active())
        except Exception:
            session_on = False
        if session_on:
            self._mic_button.configure(
                text="●",
                fg_color=STATE_COLOURS["recording"],
                text_color=FG_PRIMARY,
                hover_color="#dc2626",
                border_width=0,
            )
        else:
            self._mic_button.configure(
                text="◉",
                fg_color="transparent",
                text_color=FG_SECONDARY,
                hover_color=BG_MENU_HOVER,
                border_width=2,
                border_color=FG_SECONDARY,
            )

    def _poll_mode(self) -> None:
        # Re-render the mode + language live so auto-routing reflects
        # the focused app as the user switches between windows.
        if not self._paused:
            self._refresh()
        if self._root:
            self._root.after(1500, self._poll_mode)

    def _eq_tick(self) -> None:
        # Stop when the mic goes idle. Re-entry happens via _refresh when
        # state flips back to a live state.
        if self._state not in ("recording", "session") or self._paused or self._root is None:
            self._eq_tick_scheduled = False
            return
        try:
            level = max(0.0, min(1.0, float(self._get_audio_level())))
        except Exception:
            level = 0.0
        self._eq_history.append(level)
        if self._eq_canvas is not None and self._eq_bars:
            for i, lvl in enumerate(self._eq_history):
                h = max(2, min(_EQ_H, int(lvl * _EQ_GAIN)))
                x = i * (_EQ_BAR_W + _EQ_BAR_GAP)
                self._eq_canvas.coords(
                    self._eq_bars[i],
                    x, _EQ_H - h, x + _EQ_BAR_W, _EQ_H,
                )
        self._root.after(_EQ_TICK_MS, self._eq_tick)

    # ------------------------------------------------------------------
    # Compact mode
    # ------------------------------------------------------------------

    def _apply_compact_and_persist(self) -> None:
        state = load_state()
        state["compact"] = self._compact
        _save_state(state)
        self._refresh()
        self._apply_compact_height()

    def _apply_compact_height(self) -> None:
        """Resize the window to fit whatever rows _refresh() left visible.

        Height is read back from Tk's own layout (winfo_reqheight) rather
        than a hardcoded constant, so it always matches the actual packed
        content. Uses the same raw wm-geometry call as _move_window, for
        the same reason: CTk's Python-level geometry() can silently misapply
        DPI scaling on Windows for a plain resize.
        """
        if self._root is None:
            return
        self._root.update_idletasks()
        x = self._root.winfo_x()
        y = self._root.winfo_y()
        if self._compact and not self._hover_expanded:
            target_h = self._root.winfo_reqheight()
        else:
            target_h = _H
        try:
            self._root.tk.call("wm", "geometry", self._root._w, f"{_W}x{target_h}+{x}+{y}")
        except Exception:
            pass

    def _bind_hover(self, widget) -> None:
        """Bind Enter/Leave on a widget (and its inner canvas, if any) for
        compact-mode hover-expand. add='+' so these never clobber any other
        bindings already on shared widgets (e.g. the drag bindings)."""
        targets = [widget]
        inner = getattr(widget, "_canvas", None)
        if inner is not None and inner is not widget:
            targets.append(inner)
        for t in targets:
            try:
                t.bind("<Enter>", self._on_hover_enter, add="+")
                t.bind("<Leave>", self._on_hover_leave, add="+")
            except Exception:
                pass

    def _cancel_hover_collapse(self) -> None:
        if self._hover_collapse_job is not None and self._root is not None:
            try:
                self._root.after_cancel(self._hover_collapse_job)
            except Exception:
                pass
            self._hover_collapse_job = None

    def _on_hover_enter(self, _event=None) -> None:
        if not self._compact:
            return
        self._cancel_hover_collapse()
        if not self._hover_expanded:
            self._hover_expanded = True
            self._refresh()
            self._apply_compact_height()

    def _on_hover_leave(self, _event=None) -> None:
        if not self._compact or not self._hover_expanded or self._root is None:
            return
        # Moving between the gadget's own child widgets fires a spurious
        # Leave at every internal boundary crossing (each CTk/tk widget is
        # its own window under the hood); only actually schedule a collapse
        # once the pointer is outside the window's own screen rectangle.
        try:
            px, py = self._root.winfo_pointerx(), self._root.winfo_pointery()
            wx, wy = self._root.winfo_rootx(), self._root.winfo_rooty()
            ww, wh = self._root.winfo_width(), self._root.winfo_height()
        except Exception:
            return
        if wx <= px <= wx + ww and wy <= py <= wy + wh:
            return
        self._cancel_hover_collapse()
        self._hover_collapse_job = self._root.after(
            _HOVER_COLLAPSE_DELAY_MS, self._collapse_after_hover
        )

    def _collapse_after_hover(self) -> None:
        self._hover_collapse_job = None
        self._hover_expanded = False
        self._refresh()
        self._apply_compact_height()

    def _persist_forced_and_refresh(self, mode: Optional[str]) -> None:
        state = load_state()
        state["forced_mode"] = mode
        _save_state(state)
        self._refresh()

    def _persist_translate_and_refresh(self, on: bool) -> None:
        state = load_state()
        state["translate_to_english"] = on
        _save_state(state)
        self._refresh()

    # ------------------------------------------------------------------
    # Mode menu
    # ------------------------------------------------------------------

    def _toggle_menu(self) -> None:
        if self._menu_top is not None and self._menu_top.winfo_exists():
            self._close_menu()
            return
        self._open_menu()

    def _open_menu(self) -> None:
        root = self._root
        if root is None:
            return

        btn = self._mode_button
        btn.update_idletasks()
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height() + 4
        w = btn.winfo_width()

        menu = ctk.CTkToplevel(root)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(fg_color=BG_MENU)
        menu.geometry(f"{w}x360+{x}+{y}")

        forced = self._get_forced()
        translate_on = self._get_translate()
        # Capture the target state at menu-open time so a duplicate fire
        # of the toggle item still resolves to the same absolute value.
        new_translate_target = not translate_on

        def _add_item(label: str, command, active: bool = False) -> None:
            row_bg = BG_MENU_ACTIVE if active else "transparent"
            row = ctk.CTkButton(
                menu, text=label, anchor="w",
                fg_color=row_bg, hover_color=BG_MENU_HOVER,
                text_color=FG_PRIMARY,
                font=(FONT_FAMILY, 11),
                corner_radius=4, height=28,
                command=command,
            )
            row.pack(fill="x", padx=4, pady=1)

        def _add_separator() -> None:
            sep = ctk.CTkFrame(menu, fg_color=SEP_COLOUR, height=1)
            sep.pack(fill="x", padx=8, pady=2)

        def _pick(mode: Optional[str]) -> None:
            self._close_menu()
            self._on_force_mode(mode)

        def _toggle_t() -> None:
            # Use absolute set, not toggle. Captured at menu-open time so
            # duplicate fires set the same value (idempotent) instead of
            # flipping back to where we started.
            self._on_set_translate(new_translate_target)
            self._close_menu()

        def _hide() -> None:
            self._close_menu()
            if self._on_hide_gadget is not None:
                self._on_hide_gadget()
            else:
                self.hide()

        def _quit() -> None:
            self._close_menu()
            self._on_quit()

        auto_mode = self._get_auto_mode()
        auto_label = f"Auto  (now: {auto_mode.replace('_', ' ')})"
        _add_item(auto_label,     lambda: _pick(None),          active=(forced is None))
        _add_separator()
        for _m in MODES:
            _add_item(
                _MODE_MENU_LABELS.get(_m, _m.replace("_", " ").title()),
                (lambda m=_m: lambda: _pick(m))(),
                active=(forced == _m),
            )
        _add_separator()
        check = "✓ " if translate_on else "    "
        _add_item(
            f"{check}Translate to British English",
            _toggle_t,
            active=translate_on,
        )
        _add_separator()

        def _toggle_compact() -> None:
            self._close_menu()
            if self._on_compact_toggle is not None:
                self._on_compact_toggle()

        compact_check = "✓ " if self._compact else "    "
        _add_item(f"{compact_check}Compact mode", _toggle_compact, active=self._compact)
        _add_separator()
        _add_item("Hide gadget", _hide)
        _add_item("Quit dictation service", _quit)

        # Dismiss menu on click anywhere else.
        menu.bind("<FocusOut>", lambda e: self._close_menu())
        menu.after(50, menu.focus_force)

        self._menu_top = menu

    def _close_menu(self) -> None:
        if self._menu_top is not None:
            try:
                self._menu_top.destroy()
            except Exception:
                pass
            self._menu_top = None

    # ------------------------------------------------------------------
    # Pause click (button, not drag-release)
    # ------------------------------------------------------------------

    def _on_pause_clicked(self) -> None:
        self._on_pause_toggle()

    # ------------------------------------------------------------------
    # Mic click: toggle session mode on/off without the Alt+1 shortcut.
    # ------------------------------------------------------------------

    def _on_mic_clicked(self) -> None:
        if self._on_session_toggle is None:
            return
        self._on_session_toggle()
        if self._root:
            self._root.after(50, self._refresh)

    # ------------------------------------------------------------------
    # Language pill click: cycle the dictation-language lock (en/fa/auto).
    # ------------------------------------------------------------------

    def _on_lang_pill_clicked(self, event=None) -> None:
        if self._on_cycle_language is None:
            return
        self._on_cycle_language()
        self._refresh()

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def _bind_drag(self, widget) -> None:
        """Bind drag handlers on a widget. CTk widgets render onto an
        internal tk.Canvas that captures clicks before the outer widget's
        bindings fire, so we bind on both the widget and the canvas when
        the canvas attribute exists."""
        targets = [widget]
        inner = getattr(widget, "_canvas", None)
        if inner is not None and inner is not widget:
            targets.append(inner)
        for t in targets:
            try:
                t.bind("<ButtonPress-1>", self._on_press)
                t.bind("<B1-Motion>", self._on_drag)
                t.bind("<ButtonRelease-1>", self._on_release)
                t.bind("<Button-3>", self._on_right_click)
            except Exception:
                pass

    # File-based drag diagnostics. Pythonw has no stderr so we log to disk.
    # Reset every press; truncated to keep things readable.

    def _drag_log(self, line: str) -> None:
        try:
            drag_log_path = logs_dir() / "drag-debug.log"
            with open(drag_log_path, "a", encoding="utf-8") as f:
                from datetime import datetime
                f.write(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {line}\n")
        except Exception:
            pass

    def _move_window(self, new_x: int, new_y: int) -> None:
        """Move the window via the raw Tcl wm geometry command. CTk's
        Python-level geometry() override applies DPI scaling that can
        silently fail for position-only ('+x+y') updates on Windows."""
        if self._root is None:
            return
        try:
            self._root.tk.call("wm", "geometry", self._root._w, f"+{new_x}+{new_y}")
        except Exception:
            # Fall back to CTk's geometry() if the raw call ever fails.
            try:
                self._root.geometry(f"+{new_x}+{new_y}")
            except Exception:
                pass

    def _on_press(self, event: tk.Event) -> None:
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root
        self._dragging = False
        # Cursor offset from window origin at press time. The drag handler
        # uses this to compute the new window position from the cursor's
        # absolute screen position, which is robust to lost events and to
        # winfo_x()/winfo_y() lagging behind successive geometry() calls.
        if self._root is not None:
            self._drag_offset_x = event.x_root - self._root.winfo_x()
            self._drag_offset_y = event.y_root - self._root.winfo_y()
        widget_cls = event.widget.__class__.__name__ if event.widget is not None else "?"
        self._drag_log(
            f"PRESS    widget={widget_cls:>20}  root=({event.x_root},{event.y_root})  "
            f"offset=({self._drag_offset_x},{self._drag_offset_y})"
        )
        # Visual press feedback on the grip so the user can confirm the
        # click is registering even before they move the mouse.
        try:
            self._grip.configure(bg="#3a3a3a")
            self._grip_label.configure(bg="#3a3a3a", fg="#f1f5f9")
        except Exception:
            pass

    def _on_drag(self, event: tk.Event) -> None:
        dx = event.x_root - self._drag_start_x
        dy = event.y_root - self._drag_start_y
        if abs(dx) > 3 or abs(dy) > 3:
            self._dragging = True
        moved = False
        if self._dragging and self._root:
            new_x = event.x_root - self._drag_offset_x
            new_y = event.y_root - self._drag_offset_y
            self._move_window(new_x, new_y)
            moved = True
        widget_cls = event.widget.__class__.__name__ if event.widget is not None else "?"
        self._drag_log(
            f"MOTION   widget={widget_cls:>20}  root=({event.x_root},{event.y_root})  "
            f"d=({dx:+d},{dy:+d})  dragging={self._dragging}  moved={moved}"
        )

    def _on_release(self, event: tk.Event) -> None:
        widget_cls = event.widget.__class__.__name__ if event.widget is not None else "?"
        self._drag_log(
            f"RELEASE  widget={widget_cls:>20}  dragging={self._dragging}"
        )
        if self._dragging and self._root:
            state = load_state()
            state["x"] = self._root.winfo_x()
            state["y"] = self._root.winfo_y()
            _save_state(state)
            self._dragging = False
        # Restore grip colour after press feedback.
        try:
            self._grip.configure(bg="#262626")
            self._grip_label.configure(bg="#262626", fg="#9ca3af")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Right-click toggles the mode menu (alternate dismiss path).
    # ------------------------------------------------------------------

    def _on_right_click(self, event: tk.Event) -> None:
        self._toggle_menu()
