"""freeflow-windows config: loads GROQ_API_KEY from environment, .env, or
%APPDATA%\\FreeFlow\\config.json.

Key lookup order (first non-empty value wins):
  1. Process environment variable GROQ_API_KEY (set externally or by a previous
     run that already loaded a .env / config.json).
  2. Project .env at the repo root (developer setup; loaded via python-dotenv).
  3. %APPDATA%\\FreeFlow\\config.json  (written by the first-run wizard on
     first use, survives reinstalls and works when the app is installed
     read-only under Program Files).

If no key is found, a small Tkinter wizard prompts the user to paste their
Groq API key and persists it to %APPDATA%\\FreeFlow\\config.json.  If the
wizard is cancelled, the app exits with a clear error message.
"""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
import tkinter.messagebox as msgbox
import webbrowser
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):  # type: ignore[misc]
        return False

PROJECT_ROOT = Path(__file__).resolve().parent

# %APPDATA%\FreeFlow\config.json — portable across reinstalls.
_APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "FreeFlow"
_APPDATA_CONFIG = _APPDATA_DIR / "config.json"

GROQ_CONSOLE_URL = "https://console.groq.com"


@dataclass
class Config:
    groq_api_key: str


# ---------------------------------------------------------------------------
# APPDATA config helpers
# ---------------------------------------------------------------------------

def _read_appdata_key() -> str:
    """Read GROQ_API_KEY from %APPDATA%\\FreeFlow\\config.json. Returns empty string on any error."""
    try:
        data = json.loads(_APPDATA_CONFIG.read_text(encoding="utf-8"))
        return str(data.get("GROQ_API_KEY", "")).strip()
    except Exception:
        return ""


def _write_appdata_key(key: str) -> None:
    """Persist key to %APPDATA%\\FreeFlow\\config.json. Never overwrites a different existing key."""
    _APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = _read_appdata_key()
    if existing and existing != key:
        # A different key is already stored; do not overwrite.
        return
    try:
        data: dict = {}
        if _APPDATA_CONFIG.exists():
            try:
                data = json.loads(_APPDATA_CONFIG.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["GROQ_API_KEY"] = key
        _APPDATA_CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"WARNING: could not save key to {_APPDATA_CONFIG}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# First-run wizard
# ---------------------------------------------------------------------------

def _run_wizard() -> str:
    """Show a small Tkinter dialog asking the user to paste their Groq API key.

    Returns the key on Save, or an empty string if the user cancels.
    """
    root = tk.Tk()
    root.title("FreeFlow: API key setup")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    # Centre the window on screen.
    root.update_idletasks()
    w, h = 460, 220
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    result: dict = {"key": ""}

    tk.Label(
        root,
        text="FreeFlow needs a Groq API key to transcribe and clean up your dictation.",
        wraplength=420,
        justify="left",
        font=("Segoe UI", 10),
        pady=8,
    ).pack(padx=20, anchor="w")

    tk.Label(
        root,
        text="Groq is free. No credit card required.",
        font=("Segoe UI", 9),
        fg="#555555",
    ).pack(padx=20, anchor="w")

    link = tk.Label(
        root,
        text=GROQ_CONSOLE_URL,
        font=("Segoe UI", 9, "underline"),
        fg="#1a56db",
        cursor="hand2",
    )
    link.pack(padx=20, anchor="w", pady=(0, 8))
    link.bind("<Button-1>", lambda _: webbrowser.open(GROQ_CONSOLE_URL))

    tk.Label(
        root,
        text="Paste your API key here:",
        font=("Segoe UI", 10),
    ).pack(padx=20, anchor="w")

    entry_var = tk.StringVar()
    entry = tk.Entry(root, textvariable=entry_var, width=55, font=("Consolas", 9))
    entry.pack(padx=20, pady=(2, 10), fill="x")
    entry.focus_set()

    def _save() -> None:
        key = entry_var.get().strip()
        if not key:
            msgbox.showerror("No key entered", "Please paste your Groq API key before saving.")
            return
        if not key.startswith("gsk_"):
            if not msgbox.askyesno(
                "Unusual key format",
                "Groq keys usually start with 'gsk_'. Save this key anyway?",
            ):
                return
        result["key"] = key
        root.destroy()

    def _cancel() -> None:
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=4)
    tk.Button(btn_frame, text="Save", command=_save, width=10, font=("Segoe UI", 10)).pack(side="left", padx=6)
    tk.Button(btn_frame, text="Cancel", command=_cancel, width=10, font=("Segoe UI", 10)).pack(side="left", padx=6)

    root.bind("<Return>", lambda _: _save())
    root.bind("<Escape>", lambda _: _cancel())
    root.protocol("WM_DELETE_WINDOW", _cancel)

    root.mainloop()
    return result["key"]


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_config() -> Config:
    # Step 1: process environment (already set by shell or a prior load).
    key = os.getenv("GROQ_API_KEY", "").strip()

    # Step 2: project .env (developer setup).
    if not key:
        load_dotenv(PROJECT_ROOT / ".env", override=True)
        key = os.getenv("GROQ_API_KEY", "").strip()

    # Step 3: %APPDATA%\FreeFlow\config.json (installer / wizard path).
    if not key:
        key = _read_appdata_key()
        if key:
            os.environ["GROQ_API_KEY"] = key

    # Step 4: first-run wizard if still no key.
    if not key:
        key = _run_wizard()
        if key:
            _write_appdata_key(key)
            os.environ["GROQ_API_KEY"] = key

    if not key:
        print(
            "ERROR: GROQ_API_KEY is not set.\n"
            "Re-run FreeFlow and enter your key in the setup dialog.\n"
            f"Get one free at {GROQ_CONSOLE_URL}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    return Config(groq_api_key=key)
