# FreeFlow for Windows

Hold-to-talk dictation for Windows 11 with sub-second cleanup. Built on Groq Whisper plus llama-3.3-70b.

---

## What it does

Hold `Alt+1` in any application. Speak. Release. The cleaned, polished text is pasted into the active window within roughly two seconds. Supports English and Persian out of the box, with automatic language detection.

Five cleanup modes pick automatically per app: polished by default, brand voice for LinkedIn and Obsidian, prompt-mode for terminals, note-mode for Telegram, raw for code editors. Each mode enforces British English, bans em-dashes, strips a list of AI-cliché phrases, and refuses to invent facts.

---

## Why it is fast

Most AI dictation tools pipe transcripts through Claude or GPT-4 for cleanup, which adds three to eight seconds of round-trip latency. FreeFlow uses Groq's sub-second inference instead: Whisper-large-v3 for transcription and llama-3.3-70b-versatile for cleanup. Total round-trip stays under two seconds for short bursts. The cleanup falls back to the raw transcript if Groq is slow, so the dictation never blocks longer than its timeout.

---

## Easy install (no Python needed)

This is the recommended path for members of the Maker's AI Lab community.

1. Download `FreeFlow-Setup.exe` from the [Releases page](https://github.com/hamedarabuk/freeflow-windows/releases).
2. Double-click the installer. Windows may show a SmartScreen warning because the file is not yet code-signed. Click **More info**, then **Run anyway**. (See [INSTALL.md](INSTALL.md) for a screenshot walkthrough.)
3. On first launch, a small setup dialog asks for your Groq API key. Get one free (no credit card) at [console.groq.com](https://console.groq.com). Paste it in and click **Save**. Done.

A system tray icon appears and a small floating gadget sits at the bottom-right of your screen. Hold `Alt+1` to dictate.

---

## Developer install (from source)

```powershell
git clone https://github.com/hamedarabuk/freeflow-windows.git
cd freeflow-windows
pip install -r requirements.txt
python main.py
```

The `keyboard` library hooks into the global Windows input stream and needs administrator rights. Run the service from an elevated PowerShell prompt, or grant UAC elevation on first run.

On first run, if no Groq API key is found in the environment, a setup dialog appears automatically. The key is stored in `%APPDATA%\FreeFlow\config.json`. Alternatively, create a `.env` file at the repo root with `GROQ_API_KEY=your_key_here`.

A system tray icon appears and a small floating gadget sits at the bottom-right of your screen. The service listens for `Alt+1` in the background.

---

## Usage

| Gesture | Action |
|---|---|
| Hold `Alt+1` | Start recording. Release to transcribe + clean + paste. |
| Double-tap `Alt+1` | Toggle session mode (continuous voice-activity-driven capture). |
| Click the mode pill | Open dropdown to lock a mode or toggle translate. |
| Click the language pill | Cycle the dictation language lock: EN -> FA -> auto. Also available from the tray's Language submenu. |
| Say "undo paste" or "scratch paste" | Undo the most recent paste (one backspace per character, capped at 2000) within 120 seconds. Also available as "Undo last paste" in the tray menu. |
| Drag the grip bar (top of gadget) | Move the gadget. Position is remembered across restarts. |
| Right-click the gadget | Open the mode dropdown. |
| Click the pause icon | Pause / resume dictation. |

The floating gadget shows: a state LED (idle / recording / processing / paused / session), a live audio equaliser whilst recording, a language pill with the last detected input language (EN, FA, FR, etc.), and the current mode. Whilst cleanup is processing, the state label grows a live elapsed-time suffix (e.g. "Processing 1.4s") once the wait passes one second, so a slow round-trip never looks frozen.

---

## Modes

| Mode | Auto-trigger | What it does |
|---|---|---|
| `polished` | Default (everything else); Outlook, Word, Thunderbird; Gmail or Outlook web in browser title | Fix filler, false starts, mis-transcriptions, grammar. Preserve speaker voice and rhythm. |
| `brand_voice` | Obsidian, Notion, LinkedIn in browser title | Short sentences, specific nouns, bottom-line up front, no marketing fluff, no AI-sounding copy. |
| `prompt` | Terminal with "claude", "ai", or "llm" in title | Reshape transcript into a terse AI instruction: goal, constraints, output shape. |
| `note` | Telegram, Slack, Teams, Discord, WhatsApp desktop apps; X/Twitter in browser title | Light touch. Fix mis-transcriptions only. Preserve casual tone, fragments, ellipses. |
| `raw` | VS Code, JetBrains IDEs | Minimal. Only fix transcription errors (homophones, garbled words). Filler and punctuation untouched. |

All modes enforce British English, ban em-dashes, strip a list of AI-cliché phrases, refuse to invent facts, and preserve Persian input as Persian output.

---

## Translate to British English

Toggle from the dropdown. When ON, the cleanup pass tells the model to translate the cleaned result into natural British English regardless of what language Whisper detected. Useful for dictating LinkedIn posts in Persian and getting clean English out. Adds roughly 300 to 500ms.

---

## Session mode (hands-free)

Middle-ground between hold-to-talk and always-listening. Double-tap `Alt+1` to enter. The mic stays open, a voice activity detector chunks each speech burst, transcribes it, and pastes it immediately. Pause between sentences as long as you like. Double-tap `Alt+1` again to exit.

While in session, the LED turns purple and the state label reads "Listening (session)". Privacy note: the mic IS open continuously during a session, but VAD only triggers transcription on confirmed speech (300ms minimum). Quit the session as soon as you are done.

Requires `webrtcvad-wheels` (already in requirements.txt). If not installed, double-tap shows a toast instead.

---

## Voice editing ("edit this")

Rewrite any text you can select, in any app, with a spoken instruction:

1. Select the text (mouse or `Ctrl+A`).
2. Hold `Alt+1` and say **"edit this: make it more formal"**, then release.
3. The selection is rewritten by the LLM and pasted in its place.

Trigger phrases: `edit this`, `rewrite this`, `edit selection`, each followed by any instruction ("turn into bullet points", "shorten this", "translate to Farsi"). Say **"undo paste"** within two minutes to reverse it. If nothing is selected you get a "No text selected" alert and your clipboard is left untouched.

---

## Meeting notes

Turn FreeFlow into a note-taker: tray icon, then **Meeting notes: start**. The microphone records in 60-second chunks and each chunk is transcribed in the background while the meeting continues. Stop it from the same menu and FreeFlow writes and opens a markdown file with a summary (bullets, decisions, action items) followed by the full transcript.

Sessions are stored in `%APPDATA%\FreeFlow\meetings\<date-time>\`, including a `transcript.partial.txt` that survives a crash mid-meeting. v1 records the microphone only (your side of a call plus whatever the mic picks up).

---

## Notifications

By default FreeFlow only toasts things that need your attention (errors, "Meeting notes saved", undo confirmations). Routine state changes (mode, language, translate, session on/off) are silent because the floating gadget already shows them. Change this from the tray: **Notifications** > All / Important only / Off (`notify_level` in `settings.json`).

---

## Customise

Two files give you full control without editing code:

- **`dictionary.json`** (copy from `dictionary.json.example`): brand names, technical jargon, anything Whisper guesses wrong. Plus a case-insensitive find-and-replace map applied after transcription. Reloads automatically when you save.
- **`snippets.json`** (copy from `snippets.json.example`): voice shortcuts. Dictate the cue, the expansion is pasted instantly, LLM cleanup is skipped. Use for Calendly links, email sign-offs, canned intros. Reloads automatically.

Three additional settings in `settings.json` are worth knowing:

| Key | Default | What it does |
|---|---|---|
| `dictation_language` | `"en"` | Forced ISO code sent to Whisper. `"en"` locks English so accented speech is never hallucinated into another script. Set to `"auto"` to detect per utterance, or `"fa"` to force Persian. |
| `quality_guard_level` | `"fast"` | `"fast"` uses word-count and edit-distance checks only. `"full"` adds cosine similarity (requires `sentence-transformers` from `requirements-optional.txt`). |
| `brand_name` | `"the user's brand"` | Your brand name, injected into the `brand_voice` mode prompt. Set this to your actual brand name (e.g. `"Silux London"`). |
| `input_backend` | `"keyboard"` | `"keyboard"` (default) requires admin rights. `"pynput"` is an experimental no-admin alternative; install `pynput` from `requirements-optional.txt` first. |

For deeper changes:

- `router.py` — edit the rules that map foreground app to cleanup mode.
- `prompts/*.txt` — edit the cleanup instructions per mode.
- `cleanup.py` — adjust timeouts, banned-phrase list, or the rewriter guard prompt.

---

## Optional autostart

To run FreeFlow at every Windows login, register a Task Scheduler task that calls `pythonw.exe main.py`. The exact XML depends on your Python install path, so the simplest path is to use Task Scheduler's GUI: trigger "At log on", action "Start a program", program `pythonw.exe`, arguments `"D:\path\to\freeflow-windows\main.py"`, working directory `"D:\path\to\freeflow-windows"`. Set Run with highest privileges so the keyboard hook works.

Use `pythonw.exe` (not `python.exe`) to avoid a console window. The service auto-starts within roughly five seconds of logon.

---

## Cost estimate

- Groq Whisper (whisper-large-v3): about $0.004 per minute of audio.
- Groq llama-3.3-70b-versatile cleanup: about $0.006 per cleanup call.
- Typical light use (50 dictations per day, 20 seconds each): roughly $0.01 to $0.05 per day.

---

## Troubleshooting

**`keyboard` raises ImportError or access denied.**
Run from an elevated PowerShell prompt. The `keyboard` library hooks the global input stream, which needs admin rights on Windows.

**`No module named 'win32gui'`.**
`pywin32` did not install cleanly. From elevated PowerShell: `pip install pywin32`, then `python Scripts\pywin32_postinstall.py -install`.

**Tray icon does not appear.**
`pystray` needs a display. Confirm the service is not running in a non-interactive Task Scheduler session.

**Cleanup returns the raw transcript.**
The cleanup falls back to the raw transcript on Groq timeout or error. Check `%APPDATA%\FreeFlow\logs\YYYY-MM-DD.jsonl` for the `fallback: true` rows and any error context. A tray toast also appears (throttled to once per ten minutes so a run of fallbacks doesn't spam you). Confirm `GROQ_API_KEY` in `.env` is valid.

**Where are the logs?**
All logs live in `%APPDATA%\FreeFlow\logs`: `YYYY-MM-DD.jsonl` (per-dictation history) and `app.log` (general application log, rotated at 1MB, 3 backups kept). Useful when `pythonw.exe` runs with no visible console.

**Transcription is slow.**
Groq Whisper latency is typically under two seconds for a 10 to 30 second clip. If response times exceed five seconds, check network connectivity to `api.groq.com`.

---

## Why I built this

I needed something like FreeFlow on Windows 11. The popular macOS-only options would not run, and the AI-subscription cleanup paths (Claude, ChatGPT) added enough latency to break the typing rhythm. Groq's sub-second inference makes the round trip feel instant. The five-mode router was a side-effect of noticing that what I want a dictation tool to do is wildly different in a terminal, an editor, LinkedIn, and a chat window. Now each app gets the cleanup it deserves.

Source extracted from a private multi-agent workbench to release as a standalone tool. MIT licensed; bug reports and PRs welcome.

---

## Licence

MIT. See `LICENSE`. Copyright (c) 2026 Hamed Arab Choobdar.
