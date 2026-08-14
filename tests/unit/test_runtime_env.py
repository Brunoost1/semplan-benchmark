from __future__ import annotations

from pathlib import Path

from semplan.runtime_env import openai_api_key


def test_openai_api_key_prefers_process_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")

    assert openai_api_key(dotenv) == "from-env"


def test_openai_api_key_reads_local_dotenv_without_required_shell_export(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    dotenv = tmp_path / ".env"
    dotenv.write_text('OPENAI_API_KEY="from-dotenv"\n', encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert openai_api_key(dotenv) == "from-dotenv"
