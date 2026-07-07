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
