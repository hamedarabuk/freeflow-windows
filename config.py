"""freeflow-windows config: loads GROQ_API_KEY from .env via python-dotenv."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):  # type: ignore[misc]
        # Fall back: rely on the OS environment if python-dotenv is missing.
        return False

PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass
class Config:
    groq_api_key: str


def load_config() -> Config:
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        print(
            "ERROR: GROQ_API_KEY is not set.\n"
            "Copy .env.example to .env and paste your Groq key "
            "(get one free at https://console.groq.com).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return Config(groq_api_key=key)
