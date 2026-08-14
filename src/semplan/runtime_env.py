"""Runtime-only environment helpers for paid local commands."""

from __future__ import annotations

import os
from pathlib import Path


def openai_api_key(dotenv_path: Path = Path(".env")) -> str | None:
    """Return an OpenAI API key from the process environment or local `.env`."""

    from_environment = os.environ.get("OPENAI_API_KEY")
    if from_environment:
        return from_environment
    if not dotenv_path.exists():
        return None
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "OPENAI_API_KEY":
            normalized = value.strip().strip("\"'")
            return normalized or None
    return None
