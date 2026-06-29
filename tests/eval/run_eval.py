"""
tests/eval/run_eval.py — FreeFlow cleanup eval harness.

Usage:
    python tests/eval/run_eval.py --dataset tests/eval/cases.sample.jsonl
    python tests/eval/run_eval.py --dataset tests/eval/cases.sample.jsonl --live

Prints per-case and aggregate metrics:
  - WER vs gold (raw Whisper, LLM cleaned)
  - WER improvement (positive = cleanup helped)
  - Hallucination rate (cleanup WER > raw WER)
  - Mean edit ratio (how much the LLM changed the text)

Requires: pip install jiwer rapidfuzz   (see requirements-dev.txt)
Optional: GROQ_API_KEY in .env for --live mode
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Optional

# Allow running from the repo root or from tests/eval/.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

try:
    import jiwer
except ImportError:
    print("ERROR: jiwer not installed. Run: pip install jiwer")
    sys.exit(1)

try:
    from rapidfuzz.distance import Levenshtein as _rf_lev
    def _edit_ratio(a: str, b: str) -> float:
        max_len = max(len(a), len(b))
        return 0.0 if max_len == 0 else _rf_lev.distance(a, b) / max_len
except ImportError:
    import difflib
    def _edit_ratio(a: str, b: str) -> float:
        max_len = max(len(a), len(b))
        if max_len == 0:
            return 0.0
        sm = difflib.SequenceMatcher(None, a, b)
        dist = max_len - int(sm.ratio() * max_len)
        return dist / max_len


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _live_clean(raw: str, mode: str, api_key: str) -> str:
    """Call the real cleanup module."""
    from cleanup import clean
    cleaned, _ = clean(raw, mode, api_key, translate_to_english=False)
    return cleaned


def _load_dataset(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _safe_wer(reference: str, hypothesis: str) -> float:
    try:
        return jiwer.wer(_nfc(reference), _nfc(hypothesis))
    except Exception:
        return float("nan")


def _safe_cer(reference: str, hypothesis: str) -> float:
    try:
        return jiwer.cer(_nfc(reference), _nfc(hypothesis))
    except Exception:
        return float("nan")


def run(dataset_path: Path, live: bool, api_key: Optional[str]) -> None:
    records = _load_dataset(dataset_path)
    print(f"Dataset: {dataset_path.name}  ({len(records)} cases)")
    print(f"Mode: {'live Groq' if live else 'pre-supplied cleaned text'}")
    print()

    header = f"{'ID':<12} {'lang':<6} {'mode':<12} {'WER_raw':>8} {'WER_cln':>8} {'WER_imp':>8} {'CER_cln':>8} {'edit_r':>7} {'halluc':>7}"
    print(header)
    print("-" * len(header))

    results = []
    for rec in records:
        case_id = rec.get("id", "?")
        raw     = rec["raw"]
        gold    = rec["gold"]
        mode    = rec.get("mode", "polished")
        lang    = rec.get("lang", "en")

        if live:
            if not api_key:
                print(f"  {case_id}: SKIP (no GROQ_API_KEY for live mode)")
                continue
            cleaned = _live_clean(raw, mode, api_key)
        else:
            cleaned = rec.get("cleaned", raw)

        wer_raw = _safe_wer(gold, raw)
        wer_cln = _safe_wer(gold, cleaned)
        cer_cln = _safe_cer(gold, cleaned)
        imp     = wer_raw - wer_cln
        er      = _edit_ratio(_nfc(raw), _nfc(cleaned))
        halluc  = wer_cln > wer_raw

        results.append({
            "id": case_id, "lang": lang, "mode": mode,
            "wer_raw": wer_raw, "wer_cln": wer_cln, "wer_imp": imp,
            "cer_cln": cer_cln, "edit_ratio": er, "hallucinated": halluc,
        })

        print(
            f"{case_id:<12} {lang:<6} {mode:<12} "
            f"{wer_raw:>8.3f} {wer_cln:>8.3f} {imp:>+8.3f} "
            f"{cer_cln:>8.3f} {er:>7.3f} {'YES' if halluc else 'no':>7}"
        )

    if not results:
        print("No results.")
        return

    n = len(results)
    mean_wer_raw  = sum(r["wer_raw"]    for r in results) / n
    mean_wer_cln  = sum(r["wer_cln"]    for r in results) / n
    mean_imp      = sum(r["wer_imp"]    for r in results) / n
    mean_cer_cln  = sum(r["cer_cln"]    for r in results) / n
    mean_er       = sum(r["edit_ratio"] for r in results) / n
    halluc_rate   = sum(1 for r in results if r["hallucinated"]) / n

    print()
    print("=" * len(header))
    print(
        f"{'MEAN':<12} {'':6} {'':12} "
        f"{mean_wer_raw:>8.3f} {mean_wer_cln:>8.3f} {mean_imp:>+8.3f} "
        f"{mean_cer_cln:>8.3f} {mean_er:>7.3f} {halluc_rate*100:>6.1f}%"
    )
    print()
    print(f"Hallucination rate : {halluc_rate*100:.1f}%  (target < 5%)")
    print(f"Mean edit ratio    : {mean_er:.3f}  (target < 0.15)")
    wer_delta = mean_wer_raw - mean_wer_cln
    direction = "improved" if wer_delta > 0 else "worsened"
    print(f"WER delta (cleanup): {wer_delta:+.3f}  ({direction} on average)")


def main() -> None:
    parser = argparse.ArgumentParser(description="FreeFlow cleanup eval harness")
    parser.add_argument(
        "--dataset",
        default="tests/eval/cases.sample.jsonl",
        help="Path to JSONL dataset file",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the real Groq cleanup API instead of using pre-supplied cleaned text",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = (_REPO / dataset_path).resolve()
    if not dataset_path.exists():
        print(f"ERROR: dataset not found: {dataset_path}")
        sys.exit(1)

    api_key: Optional[str] = None
    if args.live:
        try:
            from config import load_config
            cfg = load_config()
            api_key = cfg.groq_api_key
        except Exception:
            api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print("ERROR: --live requires GROQ_API_KEY in .env or environment")
            sys.exit(1)

    run(dataset_path, live=args.live, api_key=api_key)


if __name__ == "__main__":
    main()
