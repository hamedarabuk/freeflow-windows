"""test_edit_command.py: unit tests for the voice edit-selection feature.

Covers _match_edit_command (trigger detection + payload split),
_stage_edit_command's no-selection path, happy path, and the oversize-
selection guard. Mocks at the module seams (main.keyboard, main.pyperclip,
main.edit_text, main.paste_text) so no real keystroke, clipboard write, or
network call ever happens, following the seam-mocking style of
tests/test_dispatch.py and tests/test_undo_paste.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main


@pytest.mark.parametrize(
    "text, expected",
    [
        ("edit this: make it more formal", "make it more formal"),
        ("rewrite this as bullet points", "as bullet points"),
        ("edit selection, fix the grammar", "fix the grammar"),
        ("hello world", None),
    ],
)
def test_match_edit_command_trigger_detection(text, expected):
    assert main._match_edit_command(text) == expected


@pytest.fixture(autouse=True)
def reset_paste_state():
    """Undo-paste globals must not leak between tests."""
    main._last_paste_len = 0
    main._last_paste_ts = 0.0
    yield
    main._last_paste_len = 0
    main._last_paste_ts = 0.0


@pytest.fixture
def fake_keyboard(monkeypatch):
    """Replace main.keyboard wholesale. ctrl+c is a no-op by default (no
    selection materialises on the clipboard); individual tests can attach a
    side effect via `sent.on_send`."""
    sent = SimpleNamespace(events=[], on_send=None)

    def _send(key):
        sent.events.append(key)
        if sent.on_send:
            sent.on_send(key)

    monkeypatch.setattr(main, "keyboard", SimpleNamespace(send=_send))
    return sent


@pytest.fixture
def fake_clipboard(monkeypatch):
    """A minimal in-memory clipboard standing in for main.pyperclip."""
    state = {"value": "original clipboard content"}

    def _copy(text):
        state["value"] = text

    def _paste():
        return state["value"]

    monkeypatch.setattr(main, "pyperclip", SimpleNamespace(copy=_copy, paste=_paste))
    return state


@pytest.fixture
def fake_tray(monkeypatch):
    notifications = []
    monkeypatch.setattr(
        main, "_tray",
        SimpleNamespace(
            notify=lambda msg, important=False: notifications.append(msg),
            set_processing=lambda: None,
        ),
    )
    return notifications


@pytest.fixture
def env(monkeypatch, tmp_path, fake_keyboard, fake_clipboard, fake_tray):
    """Neutralise every dispatch seam ahead of _stage_edit_command, mirroring
    tests/test_dispatch.py's env fixture. transcribe returns an edit-trigger
    utterance by default so the pipeline reaches _stage_edit_command."""
    calls = SimpleNamespace(events=[], paste=[], append=[], finalise=[])
    calls.transcribe_return = ("edit this: make it more formal", "en")

    monkeypatch.setattr(main, "_get_foreground_info", lambda: ("", ""))
    monkeypatch.setattr(main, "_match_capture_command", lambda text: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    def fake_transcribe(wav, api_key):
        calls.events.append("transcribe")
        return calls.transcribe_return

    def fake_paste(text):
        calls.events.append("paste")
        calls.paste.append(text)

    def fake_append(**kwargs):
        calls.events.append("append")
        calls.append.append(kwargs)

    def spy_finalise(outcome):
        calls.events.append("finalise")
        calls.finalise.append(outcome)

    monkeypatch.setattr(main, "transcribe", fake_transcribe)
    monkeypatch.setattr(main, "paste_text", fake_paste)
    monkeypatch.setattr(main, "append", fake_append)
    monkeypatch.setattr(main, "_finalise", spy_finalise)

    calls.wav = tmp_path / "burst.wav"
    calls.wav.write_bytes(b"RIFF....WAVEfmt ")
    calls.keyboard = fake_keyboard
    calls.clipboard = fake_clipboard
    calls.tray = fake_tray
    return calls


def test_no_selection_notifies_and_restores_clipboard(env):
    """Ctrl+C never materialises a selection (no on_send side effect): the
    stage notifies "No text selected", never pastes, and restores the
    original clipboard content it saved before clearing it."""
    main._dispatch(env.wav)

    assert "paste" not in env.events
    assert env.tray == ["No text selected"]
    assert env.clipboard["value"] == "original clipboard content"
    assert env.finalise[0].kind == "edit_command_no_selection"


def test_happy_path_pastes_rewritten_text(env, monkeypatch):
    """Ctrl+C materialises a selection on the clipboard; cleanup.edit_text
    is mocked to return a rewritten string, which is pasted. The clipboard
    is left holding the pasted text per the existing paste_text convention
    (not restored to the pre-capture original)."""
    def _on_send(key):
        if key == "ctrl+c":
            env.clipboard["value"] = "hey can u send this over thx"

    env.keyboard.on_send = _on_send
    monkeypatch.setattr(
        main, "edit_text",
        lambda selection, instruction, api_key: "Please could you send this over? Thank you.",
    )

    main._dispatch(env.wav)

    assert env.paste == ["Please could you send this over? Thank you."]
    assert env.tray == []
    assert env.finalise[0].kind == "edit_command"
    assert main._last_paste_len == len("Please could you send this over? Thank you.")


def test_oversize_selection_rejected(env, monkeypatch):
    """A selection over the 8000-char guard is rejected without ever calling
    cleanup.edit_text or pasting; the original clipboard is restored."""
    oversize = "x" * 8001

    def _on_send(key):
        if key == "ctrl+c":
            env.clipboard["value"] = oversize

    env.keyboard.on_send = _on_send

    edit_text_calls = []
    monkeypatch.setattr(
        main, "edit_text",
        lambda selection, instruction, api_key: edit_text_calls.append(1) or "should not reach",
    )

    main._dispatch(env.wav)

    assert edit_text_calls == []
    assert "paste" not in env.events
    assert env.tray == ["Selection too large"]
    assert env.clipboard["value"] == "original clipboard content"
    assert env.finalise[0].kind == "edit_command_too_large"
