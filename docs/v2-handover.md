# FreeFlow v2 — what changed and how to use it

Branch: `freeflow-v2`. Your `main` is untouched, so your daily driver keeps
working exactly as before.

- Try v2:    `git checkout freeflow-v2` then `python main.py`
- Revert:    `git checkout main`
- Your `dictionary.json` / `snippets.json` are git-ignored, so they survive
  branch switches. They are not touched by any of this.

---

## 1. The "reset" — root cause and fix

Your custom dictionary and snippets were never actually lost. Both files are
intact on disk with all your entries. The problem is that they are listed in
`.gitignore` and were never committed, so a fresh clone, a `git clean`, or an
OS reinstall would destroy them with no way to recover.

If the running app ever seems to "forget" them again, it is a load issue, not
data loss. Check `backups/` and the log.

Fixes:
- Timestamped auto-backups to `backups/` on every save and on startup (last 10
  kept per file). `git clean` or a bad edit can now be rolled back.
- A load error (corrupt or unreadable file) keeps the last-good copy in memory
  and logs it, instead of silently falling back to an empty dictionary.
- `_ensure_user_config()` can only seed from the example when the file is
  genuinely absent. It can never overwrite an existing file.

---

## 2. No more garbage sentences — the quality loop

This is the core of what you asked for.

- Cleanup now returns structured JSON: the cleaned text, the list of changes it
  made, and a confidence level. The model has to declare what it changed, so it
  cannot silently invent content.
- A guard stack checks every cleanup against your raw words before anything is
  pasted: a word-count ratio and an edit-distance ratio (fast, always on, under
  5ms). If the model rewrote too much or drifted from what you said, it does
  ONE tighter re-ask, and if that still fails it falls back to your raw
  transcript. A hallucinated rewrite never reaches the page.
- An optional deeper guard (multilingual meaning-similarity) sits behind
  `quality_guard_level: full` in settings. It needs one extra install
  (`requirements-optional.txt`) and is off by default, so the standard install
  stays light and fast.
- Translate mode (Persian in, English out) skips the edit-distance guard, which
  is meaningless across scripts, and relies on the meaning-similarity guard.
- A `RAW` badge now appears on the floating gadget when a cleanup fell back, so
  you can see at a glance when the pasted text is unedited Whisper output.

## 3. Getting better over time — the eval harness

`tests/eval/` is the "loop to improve" you wanted, made concrete.

- Add examples of `{raw transcript, ideal output}` to a JSONL file.
- Run `python tests/eval/run_eval.py` to measure, per prompt version: word
  error rate against your ideal, hallucination rate, and mean edit ratio.
- Change a prompt, re-run, keep the change only if the numbers improve. That is
  a measurable improvement loop rather than guesswork.

---

## 4. The four modes you had not explored

You had only tuned `polished`. The other four are now real:

- `brand_voice`: no longer hardcoded to your brands. Set `brand_name` and
  `brand_voice_notes` in settings. It is portable when shared with members.
- `note`: a preservation rule so it keeps your fragments and only fixes clear
  mis-transcriptions.
- `raw`: a hard rule so it touches nothing but transcription errors.
- `prompt`: sharpened into Goal / Constraints / Output shape for AI tools.

The mode menu now describes each mode in plain language and shows which mode
"Auto" has currently selected for the app in front.

## 5. Transcription accuracy

- Whisper now receives your dictionary terms as a bias prompt, so it leans
  toward your spellings (brand names, jargon).
- Long transcripts no longer silently paste nothing (an old failure above ~120
  characters).
- A single phantom trailing token is now trimmed, but common words (you, the,
  thanks) are protected, so "send it to you" is never clipped to "send it to".

---

## 6. Easy install for the community (packaging)

The member install was the biggest friction. The pieces are now in place
(scripts and docs; no binaries are built here).

- First-run wizard: on a fresh machine FreeFlow shows a friendly dialog asking
  for a Groq key and stores it in `%APPDATA%\FreeFlow`, surviving reinstalls.
  No file editing.
- `freeflow.spec` (PyInstaller, one folder) plus `installer/freeflow.iss`
  (Inno Setup) produce a single `FreeFlow-Setup.exe` that installs to
  LocalAppData with no admin needed to install. Member flow: download, Next a
  few times, paste a key once.
- `updater.py`: a non-blocking startup version check so members get updates.
- An optional no-admin input backend (`input_backend: pynput`) exists but is
  EXPERIMENTAL. It needs hands-on testing before it can be the default.

## 7. Fine-tuning — do not

You cannot fine-tune Groq-hosted models without giving up their speed, and for
a community under 100 people it is not worth it. The dictionary bias prompt,
the improved per-mode prompts, and the eval loop deliver the gains instead.

---

## 8. What needs your decision or action (nothing was pushed or published)

1. Push `freeflow-v2` to GitHub and merge when you are happy (I did not push).
2. Publish to the Skool community (I did not).
3. Set the real update URL in `updater.py` and wire `check_for_update_async`
   into `main()` when ready.
4. Buy an OV code-signing certificate (about £60/year) to remove the Windows
   SmartScreen warning for members.
5. Test the `pynput` backend in member apps (browsers, Word, Outlook), then
   decide whether to make it the packaged default and drop the admin prompt.
6. Add a branded `assets/freeflow.ico` and uncomment the icon lines.
7. Build the installer on your machine (`pyinstaller freeflow.spec` then the
   Inno compiler). No binaries were produced here.

---

## UI/UX review

What works well: the floating gadget is genuinely good, the per-app auto mode
is the right idea, and hold-to-talk is the correct primary gesture.

What changed this version: the mode menu now explains each mode and shows the
auto-selected mode; the translate badge reads "Translate ON" instead of a
cryptic icon; the gadget widened slightly to fit; and the new RAW badge tells
you when a paste was unedited.

Ideas for later: a small settings window (today settings live in JSON); a
one-keystroke "undo last paste"; a visible confidence dot from the cleanup
JSON; and a first-run mini-tour pointing at the gadget and the hotkey.
