"""
history.py — append dictation events to logs/YYYY-MM-DD.jsonl.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"


def _today_log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / f"{date_str}.jsonl"


def append(
    *,
    mode_auto: str,
    mode_forced: Optional[str],
    language: str,
    transcript_raw: str,
    transcript_clean: str,
    app_process: str,
    app_title: str,
    ms_record: int,
    ms_transcribe: int,
    ms_cleanup: int,
    ms_total: int,
    fallback: bool,
) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode_auto": mode_auto,
        "mode_forced": mode_forced,
        "language": language,
        "transcript_raw": transcript_raw,
        "transcript_clean": transcript_clean,
        "app_process": app_process,
        "app_title": app_title,
        "ms_record": ms_record,
        "ms_transcribe": ms_transcribe,
        "ms_cleanup": ms_cleanup,
        "ms_total": ms_total,
        "fallback": fallback,
    }
    path = _today_log_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def last_ten() -> list[dict]:
    path = _today_log_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records[-10:]


def log_dir() -> Path:
    return LOG_DIR
