# FreeFlow Eval Harness

Offline evaluation for the LLM cleanup step. Measures whether the cleanup
model helps or hurts transcription accuracy, and flags hallucinations.

## Dataset format

`cases.sample.jsonl` (and any custom dataset you build) is a JSONL file where
each line is a JSON object with these fields:

| Field      | Type   | Required | Notes |
|------------|--------|----------|-------|
| `raw`      | string | yes      | Whisper raw output (the input to cleanup) |
| `gold`     | string | yes      | Human-corrected gold-standard transcript |
| `mode`     | string | yes      | Cleanup mode: `polished`, `brand_voice`, `prompt`, `note`, `raw` |
| `lang`     | string | yes      | Language code: `en`, `fa`, or `mixed` |
| `cleaned`  | string | no       | Pre-supplied LLM output; used when no Groq key is present |

## Metrics

- **WER** (Word Error Rate): `jiwer.wer(gold, hypothesis)`. Lower is better.
- **CER** (Character Error Rate): `jiwer.cer(gold, hypothesis)`. More reliable for Persian.
- **WER improvement**: `wer_raw - wer_clean`. Positive means cleanup helped.
- **Hallucination rate**: percentage of samples where `wer_clean > wer_raw` (cleanup
  made things worse relative to gold).
- **Mean edit ratio**: average normalised Levenshtein distance between raw and cleaned.
  Target < 0.15.

## Running

```
# Install dev deps first:
pip install -r requirements-dev.txt
pip install -r requirements.txt   # rapidfuzz already included

# Run against the sample dataset using pre-supplied cleaned text (no API call):
python tests/eval/run_eval.py --dataset tests/eval/cases.sample.jsonl

# Run with live Groq cleanup (requires GROQ_API_KEY in .env):
python tests/eval/run_eval.py --dataset tests/eval/cases.sample.jsonl --live
```

## Building your own dataset

1. Collect real Whisper outputs from `logs/YYYY-MM-DD.jsonl` (each record has `raw`).
2. Manually correct each one to produce the `gold` field.
3. Add to a `.jsonl` file, one record per line.
4. Skew toward edge cases: Persian input, heavy filler, domain terms, short phrases.

Gate deployment on: hallucination rate < 5%, mean edit ratio < 0.15.
