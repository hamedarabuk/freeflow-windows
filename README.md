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

## Install

```powershell
git clone https://github.com/hamedarabuk/freeflow-windows.git
cd freeflow-windows
pip install -r requirements.txt
copy .env.example .env
# Edit .env and paste your Groq API key (free at https://console.groq.com)
python main.py
```

The `keyboard` library hooks into the global Windows input stream and needs administrator rights. Run the service from an elevated PowerShell prompt, or grant UAC elevation on first run.

A system tray icon appears and a small floating gadget sits at the bottom-right of your screen. The service listens for `Alt+1` in the background.

---

## Usage

| Gesture | Action |
|---|---|
| Hold `Alt+1` | Start recording. Release to transcribe + clean + paste. |
| Double-tap `Alt+1` | Toggle session mode (continuous voice-activity-driven capture). |
| Click the mode pill | Open dropdown to lock a mode or toggle translate. |
| Drag the grip bar (top of gadget) | Move the gadget. Position is remembered across restarts. |
| Right-click the gadget | Open the mode dropdown. |
| Click the pause icon | Pause / resume dictation. |

The floating gadget shows: a state LED (idle / recording / processing / paused / session), a live audio equaliser whilst recording, a language pill with the last detected input language (EN, FA, FR, etc.), and the current mode.

---

## Modes

| Mode | Auto-trigger | What it does |
|---|---|---|
| `polished` | Default (everything else) | Fix filler, false starts, mis-transcriptions, grammar. Preserve speaker voice and rhythm. |
| `brand_voice` | Obsidian, LinkedIn in browser title | Short sentences, specific nouns, bottom-line up front, no marketing fluff, no AI-sounding copy. |
| `prompt` | Terminal with "claude", "ai", or "llm" in title | Reshape transcript into a terse AI instruction: goal, constraints, output shape. |
| `note` | Telegram desktop app | Light touch. Fix mis-transcriptions only. Preserve casual tone, fragments, ellipses. |
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

## Customise

Two files give you full control without editing code:

- **`dictionary.json`** (copy from `dictionary.json.example`): brand names, technical jargon, anything Whisper guesses wrong. Plus a case-insensitive find-and-replace map applied after transcription. Reloads automatically when you save.
- **`snippets.json`** (copy from `snippets.json.example`): voice shortcuts. Dictate the cue, the expansion is pasted instantly, LLM cleanup is skipped. Use for Calendly links, email sign-offs, canned intros. Reloads automatically.

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
The cleanup falls back to the raw transcript on Groq timeout or error. Check `logs/YYYY-MM-DD.jsonl` for the `fallback: true` rows and any error context. Confirm `GROQ_API_KEY` in `.env` is valid.

**Transcription is slow.**
Groq Whisper latency is typically under two seconds for a 10 to 30 second clip. If response times exceed five seconds, check network connectivity to `api.groq.com`.

---

## Why I built this

I needed something like FreeFlow on Windows 11. The popular macOS-only options would not run, and the AI-subscription cleanup paths (Claude, ChatGPT) added enough latency to break the typing rhythm. Groq's sub-second inference makes the round trip feel instant. The five-mode router was a side-effect of noticing that what I want a dictation tool to do is wildly different in a terminal, an editor, LinkedIn, and a chat window. Now each app gets the cleanup it deserves.

Source extracted from a private multi-agent workbench to release as a standalone tool. MIT licensed; bug reports and PRs welcome.

---

## Licence

MIT. See `LICENSE`. Copyright (c) 2026 Hamed Arab Choobdar.
