from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_check_secrets() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/check_secrets.py"
    spec = importlib.util.spec_from_file_location("check_secrets_for_test", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load check_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_secret_scan_detects_deliberate_fixture() -> None:
    fake_secret = "sk-" + ("A" * 24)

    assert _load_check_secrets().contains_secret(fake_secret)


def test_secret_scan_allows_plain_variable_names() -> None:
    assert not _load_check_secrets().contains_secret(
        "OPENAI_API_KEY is a variable name, not a secret value"
    )
