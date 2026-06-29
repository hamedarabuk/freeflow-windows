"""
quality_guard.py — post-cleanup quality checks for FreeFlow.

Guard stack (in order):
  1. Fast guards (word-count ratio + normalised edit distance). Synchronous,
     target <5 ms. Always run.
  2. Semantic similarity guard. Cosine similarity via sentence-transformers
     'paraphrase-multilingual-MiniLM-L12-v2'. Lazy-loaded; silently skipped
     if the library or model is unavailable. Only runs when
     settings.quality_guard_level == 'full'.

Translate mode: edit-ratio and word-ratio guards are meaningless across
scripts (Persian -> English). When translate=True, fast guards are skipped
and only the semantic guard is applied (with a lower threshold of 0.82).

Loop logic:
  - check() returns a GuardResult.
  - accepted=True: use the cleaned text.
  - reask=True:    caller should retry with a tighter prompt, then call
                   check() again. If the retry also fails, accepted=False
                   and the caller must fall back to raw.
  - accepted=False, reask=False: fall back to raw immediately.

Async judge: log_async() fires-and-forgets a background thread that writes
a quality record to logs/quality-YYYY-MM-DD.jsonl, optionally calling a
cheap Groq judge model for offline faithfulness scoring.
"""

from __future__ import annotations

import json
import logging
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_WORD_RATIO_LO   = 0.75
_WORD_RATIO_HI   = 1.25
_EDIT_RATIO_MAX  = 0.20
_SEM_THRESHOLD   = 0.90   # same-language
_SEM_TRANSLATE   = 0.82   # cross-script translation

# ---------------------------------------------------------------------------
# Optional: sentence-transformers semantic guard
# ---------------------------------------------------------------------------
_sem_model       = None      # lazy-loaded on first use
_sem_model_tried = False     # set to True after the first load attempt
_sem_lock        = threading.Lock()

def _load_sem_model():
    global _sem_model, _sem_model_tried
    with _sem_lock:
        if _sem_model_tried:
            return _sem_model
        _sem_model_tried = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            _sem_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            log.info("quality_guard: semantic model loaded")
        except Exception as exc:
            log.info("quality_guard: sentence-transformers unavailable, semantic guard disabled (%s)", exc)
            _sem_model = None
        return _sem_model


def _semantic_sim(raw: str, cleaned: str) -> Optional[float]:
    model = _load_sem_model()
    if model is None:
        return None
    try:
        from sentence_transformers import util  # type: ignore
        embs = model.encode([raw, cleaned], convert_to_tensor=True)
        return float(util.cos_sim(embs[0], embs[1]))
    except Exception as exc:
        log.warning("quality_guard: semantic similarity failed (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# Edit-distance helpers
# ---------------------------------------------------------------------------

def _nfc(text: str) -> str:
    """NFC-normalise to keep Unicode consistent across scripts."""
    return unicodedata.normalize("NFC", text)


def _edit_ratio(raw: str, cleaned: str) -> float:
    a, b = _nfc(raw), _nfc(cleaned)
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 0.0
    try:
        from rapidfuzz.distance import Levenshtein  # type: ignore
        dist = Levenshtein.distance(a, b)
    except ImportError:
        import difflib
        sm = difflib.SequenceMatcher(None, a, b)
        dist = max_len - int(sm.ratio() * max_len)
    return dist / max_len


def _word_ratio(raw: str, cleaned: str) -> float:
    r = len(raw.split())
    c = len(cleaned.split())
    if r == 0:
        return 1.0
    return c / r


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GuardResult:
    accepted: bool
    reask: bool          # True on first failure -> caller retries once
    word_ratio: Optional[float] = None
    edit_ratio_val: Optional[float] = None
    sem_sim: Optional[float] = None
    failed_guard: str = ""    # name of first guard that failed, or ""
    confidence: str = ""      # from the LLM JSON: HIGH/MEDIUM/LOW


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check(
    raw: str,
    cleaned: str,
    confidence: str = "",
    translate_mode: bool = False,
    guard_level: str = "fast",
    *,
    is_retry: bool = False,
) -> GuardResult:
    """Run guard stack and return a GuardResult.

    Pass is_retry=True on the second call so the loop does not cycle again.
    """
    word_ratio = None
    edit_r = None
    sem = None

    if not translate_mode:
        # Fast guards
        word_ratio = _word_ratio(raw, cleaned)
        edit_r = _edit_ratio(raw, cleaned)

        if not (_WORD_RATIO_LO <= word_ratio <= _WORD_RATIO_HI):
            return GuardResult(
                accepted=False,
                reask=not is_retry,
                word_ratio=word_ratio,
                edit_ratio_val=edit_r,
                failed_guard="word_ratio",
                confidence=confidence,
            )

        if edit_r > _EDIT_RATIO_MAX:
            return GuardResult(
                accepted=False,
                reask=not is_retry,
                word_ratio=word_ratio,
                edit_ratio_val=edit_r,
                failed_guard="edit_ratio",
                confidence=confidence,
            )

    # Semantic guard (optional, full level only)
    if guard_level == "full":
        threshold = _SEM_TRANSLATE if translate_mode else _SEM_THRESHOLD
        sem = _semantic_sim(raw, cleaned)
        if sem is not None and sem < threshold:
            return GuardResult(
                accepted=False,
                reask=not is_retry,
                word_ratio=word_ratio,
                edit_ratio_val=edit_r,
                sem_sim=sem,
                failed_guard="semantic_sim",
                confidence=confidence,
            )

    return GuardResult(
        accepted=True,
        reask=False,
        word_ratio=word_ratio,
        edit_ratio_val=edit_r,
        sem_sim=sem,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Async judge log (fire-and-forget)
# ---------------------------------------------------------------------------

_LOGS_DIR = Path(__file__).resolve().parent / "logs"

def log_async(
    *,
    mode: str,
    translate: bool,
    raw: str,
    cleaned: str,
    chosen: str,
    result: GuardResult,
    outcome: str,            # "accepted" | "fallback" | "reask"
    api_key: Optional[str] = None,
    judge_model: str = "llama-3.1-8b-instant",
) -> None:
    """Fire-and-forget background thread. Never blocks the paste path."""
    t = threading.Thread(
        target=_judge_and_write,
        kwargs=dict(
            mode=mode,
            translate=translate,
            raw=raw,
            cleaned=cleaned,
            chosen=chosen,
            result=result,
            outcome=outcome,
            api_key=api_key,
            judge_model=judge_model,
        ),
        daemon=True,
        name="quality-judge",
    )
    t.start()


def _judge_and_write(
    *,
    mode: str,
    translate: bool,
    raw: str,
    cleaned: str,
    chosen: str,
    result: GuardResult,
    outcome: str,
    api_key: Optional[str],
    judge_model: str,
) -> None:
    faithfulness: Optional[float] = None

    if api_key:
        try:
            import requests  # already in requirements
            payload = {
                "model": judge_model,
                "temperature": 0,
                "max_tokens": 16,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a transcript quality judge. "
                            "Reply with exactly: PASS or FAIL:<one-word reason>."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"RAW: {raw[:500]}\n\nCLEANED: {cleaned[:500]}\n\n"
                            "Does the cleaned version contain ANY information not present in the raw?"
                        ),
                    },
                ],
            }
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=5.0,
            )
            resp.raise_for_status()
            verdict = resp.json()["choices"][0]["message"]["content"].strip()
            faithfulness = 1.0 if verdict.upper().startswith("PASS") else 0.0
        except Exception as exc:
            log.debug("quality_guard judge call failed: %s", exc)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "translate": translate,
        "raw": raw,
        "cleaned": cleaned,
        "chosen": chosen,
        "confidence": result.confidence,
        "outcome": outcome,
        "word_ratio": result.word_ratio,
        "edit_ratio": result.edit_ratio_val,
        "sem_sim": result.sem_sim,
        "failed_guard": result.failed_guard,
        "faithfulness": faithfulness,
    }

    try:
        _LOGS_DIR.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_path = _LOGS_DIR / f"quality-{date_str}.jsonl"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("quality_guard: failed to write log: %s", exc)
