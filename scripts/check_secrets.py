"""Small local secret-pattern scan for F0 CI."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bapi[_-]?key\s*=\s*['\"][^'\"]{12,}['\"]"),
]


def contains_secret(text: str) -> bool:
    """Return whether text matches a known secret pattern."""

    return any(pattern.search(text) is not None for pattern in SECRET_PATTERNS)


def _iter_text_files() -> list[Path]:
    paths: list[Path] = []
    for path in _candidate_paths():
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        paths.append(path)
    return paths


def _candidate_paths() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return list(PROJECT_ROOT.rglob("*"))
    return [PROJECT_ROOT / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def main() -> int:
    findings: list[str] = []
    for path in _iter_text_files():
        text = path.read_text(encoding="utf-8")
        if contains_secret(text):
            findings.append(str(path.relative_to(PROJECT_ROOT)))

    if findings:
        for finding in sorted(findings):
            print(f"Possible secret: {finding}", file=sys.stderr)
        return 1

    print("secret-scan ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
