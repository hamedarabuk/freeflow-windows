"""test_quality_guard.py — regression tests for quality_guard.check().

Covers the translate-mode always-on gate (length-ratio bound + meta-text
detector) and the ordinary non-translate word/edit-ratio guards.
"""

from __future__ import annotations

from quality_guard import check

# Real production translate-mode failure: the model narrated its own
# reasoning ("I should say in English...") instead of translating.
_GARBAGE_META_TEXT = (
    "I have the same feeling that I should say in English that each request "
    "in Persian has been taken and"
)


def test_translate_meta_text_rejected():
    raw = "سلام حال شما چطور است و من می خواهم این پیام را برای شما ارسال کنم"
    result = check(raw, _GARBAGE_META_TEXT, translate_mode=True)
    assert result.accepted is False
    assert result.failed_guard == "translate_meta_text"
    assert result.word_ratio is not None
    assert result.edit_ratio_val is not None


def test_translate_dictation_about_ai_not_meta_rejected():
    # Real production false positive (2026-07-13): the old bare "as an ai"
    # pattern matched legitimate dictation ABOUT AI. The tightened patterns
    # must only catch assistant self-reference phrasing.
    raw = (
        "so that AI agents would be knowledgeable and can advise people or "
        "as a AI self-consultant or AI designer jewelry designer can help them"
    )
    cleaned = (
        "So that AI agents would be knowledgeable and can advise people, or "
        "as an AI self-consultant, or AI designer, a jewelry designer can help them."
    )
    result = check(raw, cleaned, translate_mode=True)
    assert result.accepted is True


def test_translate_assistant_self_reference_still_rejected():
    raw = "سلام حال شما چطور است و من می خواهم این پیام را برای شما ارسال کنم"
    cleaned = "As an AI language model, I cannot translate this message."
    result = check(raw, cleaned, translate_mode=True)
    assert result.accepted is False
    assert result.failed_guard == "translate_meta_text"


def test_translate_legit_short_translation_accepted():
    raw = "سلام حال شما چطور است"
    cleaned = "Hello, how are you?"
    result = check(raw, cleaned, translate_mode=True)
    assert result.accepted is True
    assert result.word_ratio is not None
    assert result.edit_ratio_val is not None


def test_translate_near_empty_cleaned_rejected_on_length_ratio():
    raw = "سلام حال شما چطور است " * 10
    result = check(raw, "", translate_mode=True)
    assert result.accepted is False
    assert result.failed_guard == "translate_length_ratio"


def test_non_translate_identical_text_accepted():
    text = "This is a normal sentence about my day at work today."
    result = check(text, text, translate_mode=False)
    assert result.accepted is True


def test_non_translate_wildly_rewritten_cleaned_rejected():
    raw = (
        "This is a normal sentence about my day at work today, "
        "nothing unusual happened."
    )
    cleaned = "Nothing."
    result = check(raw, cleaned, translate_mode=False)
    assert result.accepted is False
    assert result.failed_guard in {"word_ratio", "edit_ratio"}


# Real production false fallback (2026-07-15): legitimate polished-mode
# filler removal was rejected by the old tight bounds (edit_ratio 0.30 vs
# the 0.20 cap) and the raw transcript was pasted with the RAW badge.
def test_polished_filler_removal_accepted():
    raw = "Yeah, that's much better. Yeah, we can lock it."
    cleaned = "That's much better. We can lock it."
    result = check(raw, cleaned, translate_mode=False, mode="polished")
    assert result.accepted is True


def test_polished_punctuation_polish_on_short_utterance_accepted():
    # Real production sample (2026-07-15): tiny grammar/punctuation edits on
    # a short string inflate the Levenshtein ratio (0.24 here).
    raw = "and in fact we don't mention about VAT"
    cleaned = "And in fact, we don't mention VAT."
    result = check(raw, cleaned, translate_mode=False, mode="polished")
    assert result.accepted is True


def test_polished_dropped_clause_still_rejected():
    # Real production sample (2026-07-15): the model silently dropped the
    # whole first clause. edit_ratio 0.47 must still fail the rewrite bound.
    raw = (
        "Let's make sure that I understood what you are saying and so "
        "probably I want to share what came to my mind and see if we are "
        "on the same page or not."
    )
    cleaned = (
        "I want to share what came to my mind and see if we are on the "
        "same page or not."
    )
    result = check(raw, cleaned, translate_mode=False, mode="polished")
    assert result.accepted is False
    assert result.failed_guard == "edit_ratio"


def test_raw_mode_keeps_tight_bounds():
    # 'raw' mode promises transcription-error fixes only; the same filler
    # removal that polished mode accepts must still be rejected here.
    raw = "Yeah, that's much better. Yeah, we can lock it."
    cleaned = "That's much better. We can lock it."
    result = check(raw, cleaned, translate_mode=False, mode="raw")
    assert result.accepted is False
    assert result.failed_guard == "edit_ratio"
