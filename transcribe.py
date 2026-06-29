"""
transcribe.py — Groq Whisper-large-v3 transcription client.

Uses multipart/form-data upload via requests. No openai SDK.
Returns (transcript_text, detected_language).

Biases Whisper toward known terms via the prompt parameter (dictionary.py)
and applies post-transcription substitutions for known mis-hearings.
"""

from __future__ import annotations

import logging
import requests
from pathlib import Path

from dictionary import get_terms_prompt, apply_substitutions
from settings import settings

GROQ_AUDIO_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL                   = settings.whisper_model
TIMEOUT_S               = settings.transcribe_timeout_s
_NO_SPEECH_PROB_CEILING = settings.no_speech_prob_ceiling
_SEG_NO_SPEECH          = settings.seg_no_speech
_SEG_COMPRESSION        = settings.seg_compression
_SEG_LOGPROB            = settings.seg_logprob

log = logging.getLogger(__name__)


_BUILTIN_NOISE_PATTERNS: frozenset[str] = frozenset({
    # Exact noise tokens observed in logs; add more as encountered.
    "sleuths", "sibar", "sleuth", "slepeh", "sibila", "sleptimia",
    # Common Whisper silence/noise hallucination tokens.
    "thank you", "thanks", "you", "the", ".",
    "subscribe", "subtitles", "subtitle", "transcribed", "transcription",
    "www", "http", "copyright",
})

# Merge with any extra patterns the user has added via settings.json.
_NOISE_PATTERNS: frozenset[str] = _BUILTIN_NOISE_PATTERNS | frozenset(
    p.lower().strip() for p in settings.extra_noise_patterns if p.strip()
)

# Tokens safe to strip as a SINGLE trailing phantom. Deliberately EXCLUDES
# common English words ("you", "the", "thanks", "thank you", ".") that a user
# may legitimately end a sentence on. Those are only ever treated as noise when
# they form the WHOLE burst (see _is_hallucination), never trimmed off the end
# of otherwise-valid speech. This prevents "send it to you" -> "send it to".
_TAIL_TRIM_NOISE: frozenset[str] = frozenset({
    "sleuths", "sibar", "sleuth", "slepeh", "sibila", "sleptimia",
    "subscribe", "subtitles", "subtitle", "transcribed", "transcription",
    "www", "http", "copyright",
}) | frozenset(
    p.lower().strip() for p in settings.extra_noise_patterns if p.strip()
)


def _is_hallucination(text: str) -> bool:
    """Detect Whisper's classic silence/noise hallucination patterns.

    Deliberately conservative: a false positive here silently deletes the
    user's speech with no paste and (historically) no record, which is the
    worst outcome. Real speech legitimately repeats short words ("that that",
    "no no", "go go go" is rarer but possible), so we only flag patterns that
    are overwhelmingly hallucination, not ordinary doubled words.

    Catches:
    - Single-token noise only when it is a known observed noise token.
    - 3-or-more in-a-row repetition of the same token (a true loop; ordinary
      speech almost never triples a word).
    - ABABAB+ repetition over the whole utterance (>=6 tokens of a 2-cycle).
    Does NOT flag a single doubled word (that demonstrably dropped real short
    utterances after the gate was lowered to >=4 chars on 2026-05-31).
    """
    if not text:
        return False
    tokens = text.split()
    n = len(tokens)
    lower = [t.lower().strip(".,;:!?\"'()[]{}") for t in tokens]

    # Single-token burst: only flag if it is a known noise token.
    # Real short utterances ("Yes", "Okay", "Sepehr") must pass.
    if n == 1:
        return lower[0] in _NOISE_PATTERNS

    lower = [t for t in lower if t]  # drop empties after stripping
    # 2-token case: flag only when both tokens are known noise patterns.
    # This catches "you you" / "sleuths sleuths" pairs while preserving
    # real two-word utterances like "Claude Code" or "thank you".
    if len(lower) == 2:
        return lower[0] in _NOISE_PATTERNS and lower[1] in _NOISE_PATTERNS
    if len(lower) < 3:
        return False

    # 3-in-a-row repetition of the same token (a genuine loop).
    for i in range(len(lower) - 2):
        if lower[i] and lower[i] == lower[i + 1] == lower[i + 2]:
            return True

    # Pure ABAB... loop spanning the whole short utterance (e.g. a 2-cycle
    # repeated three+ times). Requires the entire transcript to be the loop,
    # so it cannot fire on real speech that merely contains a doubled word.
    if 6 <= len(lower) <= 12:
        a, b = lower[0], lower[1]
        if a and b and a != b and all(
            lower[i] == (a if i % 2 == 0 else b) for i in range(len(lower))
        ):
            return True

    return False


def _avg_no_speech_prob(payload: dict) -> float:
    segs = payload.get("segments") or []
    probs = [
        s.get("no_speech_prob")
        for s in segs
        if isinstance(s.get("no_speech_prob"), (int, float))
    ]
    if not probs:
        return 0.0
    return sum(probs) / len(probs)


def _max_compression_ratio(payload: dict) -> float:
    segs = payload.get("segments") or []
    crs = [
        s.get("compression_ratio")
        for s in segs
        if isinstance(s.get("compression_ratio"), (int, float))
    ]
    return max(crs) if crs else 0.0


def _filter_segments(payload: dict) -> str:
    """Walk verbose_json segments and drop hallucinated ones individually.

    Whisper's tail-hallucination failure mode (valid prefix then garbage
    proper-noun list or token loop) usually lands in one or two trailing
    segments with elevated compression_ratio and depressed avg_logprob.
    Per-segment filtering keeps the valid prefix and drops only the
    suspect tail."""
    segs = payload.get("segments") or []
    if not segs:
        return (payload.get("text") or "").strip()
    kept: list[str] = []
    dropped = 0
    for s in segs:
        ns = s.get("no_speech_prob")
        cr = s.get("compression_ratio")
        ap = s.get("avg_logprob")
        if isinstance(ns, (int, float)) and ns >= _SEG_NO_SPEECH:
            dropped += 1
            continue
        if isinstance(cr, (int, float)) and cr >= _SEG_COMPRESSION:
            dropped += 1
            continue
        if isinstance(ap, (int, float)) and ap <= _SEG_LOGPROB:
            dropped += 1
            continue
        t = (s.get("text") or "").strip()
        if t:
            kept.append(t)
    if dropped:
        log.info("Dropped %d hallucinated segment(s) of %d total", dropped, len(segs))
    return " ".join(kept).strip()


def _trim_hallucination_tail(text: str) -> str:
    """Trim Whisper's tail-loop hallucinations off otherwise-valid text.

    Whisper's dominant garbage mode here is a doubled (or tripled) phantom
    proper noun appended to the END of a long valid transcript with no
    sentence terminator in front of it, e.g.
        "... a time that suits them Sibiria Sibiria"
        "... but if they are Sleptimia Sleptimia"
    The prior implementation only cut back to a preceding ". "/"! "/"? ",
    so these terminator-less tails survived and reached cleanup, which is the
    long-standing 'garbage words' symptom.

    Strategy:
    1. Token-level: drop a trailing run of repeated identical tokens (the
       loop itself), keeping one copy only if it is a real dictionary term;
       otherwise drop the whole repeated run. This removes "X X" / "X X X"
       tails directly without needing a sentence boundary.
    2. Fall back to the previous sentence-terminator trim for looser cases.
    Conservative: never trims below half the original length."""
    if not text:
        return text
    tokens = text.split()
    n = len(tokens)
    if n < 5:
        return text

    def _norm(tok: str) -> str:
        return tok.lower().strip(".,;:!?\"'()[]{}")

    # 1. Trailing identical-token run (the loop). Walk back over tokens that
    # repeat the final token.
    last_norm = _norm(tokens[-1])
    if last_norm and len(last_norm) > 2:
        run = 1
        for i in range(n - 2, -1, -1):
            if _norm(tokens[i]) == last_norm:
                run += 1
            else:
                break
        if run >= 2:
            kept = tokens[: n - run]
            trimmed = " ".join(kept).rstrip(" ,;").rstrip()
            # Re-attach terminating punctuation if the kept tail lost it.
            if trimmed and len(trimmed) >= len(text) * 0.5:
                log.info(
                    "Trimmed tail hallucination loop: %d repeats of %r",
                    run, tokens[-1],
                )
                return trimmed

    # 2. Single-occurrence phantom tail token: drop only if it is an
    # unmistakable garbage token (not a common English word) and not present in
    # the dictionary (so "Silux" at the end is preserved).
    last_norm_single = _norm(tokens[-1])
    if last_norm_single in _TAIL_TRIM_NOISE:
        from dictionary import get_terms_prompt  # late import to avoid circular
        terms_prompt = get_terms_prompt().lower()
        if last_norm_single not in terms_prompt:
            trimmed = " ".join(tokens[:-1]).rstrip(" ,;").rstrip()
            if trimmed and len(trimmed) >= len(text) * 0.5:
                log.info(
                    "Trimmed single phantom tail token %r (known noise pattern)",
                    tokens[-1],
                )
                return trimmed

    # 3. Loose tail repeat: cut back to the previous sentence terminator.
    tail = [_norm(t) for t in tokens[-4:]]
    has_loop = any(
        tail[i] and tail[i] == tail[i + 1] and len(tail[i]) > 3
        for i in range(len(tail) - 1)
    )
    if not has_loop:
        return text
    cuts = []
    for marker in (". ", "! ", "? "):
        idx = text.rfind(marker)
        if idx >= 0:
            cuts.append(idx + len(marker))
    if not cuts:
        return text
    cut = max(cuts)
    trimmed = text[:cut].rstrip()
    if not trimmed or len(trimmed) < len(text) * 0.5:
        return text
    log.info("Trimmed tail hallucination from transcript")
    return trimmed


def transcribe(wav_path: Path, api_key: str) -> tuple[str, str]:
    """Upload WAV to Groq Whisper and return (text, language).

    Returns an empty text when the burst is silence/noise so the caller
    skips the cleanup + paste round-trip."""
    data = {
        "model": MODEL,
        "response_format": "verbose_json",
    }
    prompt = get_terms_prompt()
    if prompt:
        data["prompt"] = prompt
    with open(wav_path, "rb") as f:
        response = requests.post(
            GROQ_AUDIO_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (wav_path.name, f, "audio/wav")},
            data=data,
            timeout=TIMEOUT_S,
        )
    response.raise_for_status()
    payload = response.json()
    language: str = payload.get("language", "en")

    avg_nsp = _avg_no_speech_prob(payload)
    if avg_nsp >= _NO_SPEECH_PROB_CEILING:
        log.info(
            "Dropped burst: avg no_speech_prob=%.2f, max_compression_ratio=%.2f, text=%r",
            avg_nsp, _max_compression_ratio(payload), payload.get("text", ""),
        )
        return "", language

    text = _filter_segments(payload)
    text = _trim_hallucination_tail(text)

    if _is_hallucination(text):
        log.info("Dropped burst: hallucination pattern detected, text=%r", text)
        return "", language

    text = apply_substitutions(text)
    return text, language
