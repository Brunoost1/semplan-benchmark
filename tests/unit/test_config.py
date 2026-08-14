from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from semplan.config import load_config, load_config_files
from semplan.errors import ErrorCode, ProjectError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_base_config_loads_without_provider_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    config = load_config(PROJECT_ROOT / "configs/base.yaml")

    assert config.schema_version == "1.0"
    assert config.project.reference_date.isoformat() == "2026-08-01"
    assert config.experiment.allow_paid is False
    assert config.experiment.budget_usd == Decimal("18.0")


def test_config_hash_is_stable() -> None:
    config = load_config(PROJECT_ROOT / "configs/base.yaml")

    assert config.sha256() == load_config(PROJECT_ROOT / "configs/base.yaml").sha256()
    assert (
        config.canonical_json() == load_config(PROJECT_ROOT / "configs/base.yaml").canonical_json()
    )


def test_config_overlays_are_explicit_and_ordered() -> None:
    config = load_config_files(
        [
            PROJECT_ROOT / "configs/base.yaml",
            PROJECT_ROOT / "configs/data/full.yaml",
        ]
    )

    assert config.data.profile == "full"
    assert config.provider.model == "gpt-5.6-luna"


def test_unknown_config_keys_are_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        """
schema_version: "1.0"
project:
  reference_date: "2026-08-01"
  timezone: "UTC"
data:
  seed: 20260806
  profile: small
provider:
  name: openai
  model: gpt-5.6-luna
  reasoning_effort: low
  max_output_tokens: 1200
  timeout_seconds: 60
experiment:
  repetitions: 3
  approaches: [A1, A2, A3, A4]
  budget_usd: 18.0
  allow_paid: false
execution:
  statement_timeout_ms: 5000
  max_rows: 1000
  read_only: true
unexpected: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError) as exc_info:
        load_config(invalid)

    assert exc_info.value.to_record().code is ErrorCode.CFG_INVALID


def test_missing_schema_version_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("project: {}\n", encoding="utf-8")

    with pytest.raises(ProjectError) as exc_info:
        load_config(invalid)

    assert exc_info.value.to_record().code is ErrorCode.CFG_INVALID


def test_missing_config_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProjectError) as exc_info:
        load_config(tmp_path / "missing.yaml")

    assert exc_info.value.to_record().code is ErrorCode.CFG_INVALID


def test_non_mapping_config_file_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ProjectError) as exc_info:
        load_config(invalid)

    assert exc_info.value.to_record().code is ErrorCode.CFG_INVALID


def test_empty_config_file_list_is_rejected() -> None:
    with pytest.raises(ProjectError) as exc_info:
        load_config_files([])

    assert exc_info.value.to_record().code is ErrorCode.CFG_INVALID
