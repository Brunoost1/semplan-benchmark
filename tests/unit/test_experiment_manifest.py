from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from semplan.benchmark.freeze import prepare_f7_primary_benchmark
from semplan.contracts import RunManifestStatus
from semplan.errors import ProjectError
from semplan.experiments.manifest import (
    build_fake_pilot_manifest,
    build_openai_cost_safe_manifest,
    build_openai_primary_manifest,
    copy_manifest_for_run,
    manifest_file_hash,
    validate_manifest_copy,
    validate_manifest_for_execution,
    write_run_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_fake_pilot_manifest_binds_local_artifacts() -> None:
    manifest = build_fake_pilot_manifest(
        run_id="unit-fake-pilot",
        benchmark_dir=PROJECT_ROOT / "data/benchmark/f3_smoke",
        price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
    )

    assert manifest.status is RunManifestStatus.FROZEN
    assert manifest.allow_paid is False
    assert manifest.non_reportable is True
    assert set(manifest.prompts) == set(manifest.approaches)
    assert manifest.benchmark_manifest_sha256.startswith("sha256:")


def test_reportable_frozen_manifest_rejects_dirty_tree() -> None:
    manifest = build_fake_pilot_manifest(
        run_id="unit-fake-pilot",
        benchmark_dir=PROJECT_ROOT / "data/benchmark/f3_smoke",
        price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
        non_reportable=False,
    )

    with pytest.raises(ProjectError, match="dirty working tree"):
        validate_manifest_for_execution(
            manifest,
            benchmark_dir=PROJECT_ROOT / "data/benchmark/f3_smoke",
            allow_paid=False,
            dirty_tree=True,
        )


def test_openai_primary_manifest_requires_frozen_hidden_benchmark() -> None:
    with pytest.raises(ProjectError, match="must be frozen"):
        build_openai_primary_manifest(
            run_id="unit-f7-primary",
            benchmark_dir=PROJECT_ROOT / "data/benchmark/f3_smoke",
            price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
            budget_usd=Decimal("10.00"),
        )


def test_openai_primary_manifest_binds_f7_scientific_configuration(tmp_path: Path) -> None:
    benchmark_dir = tmp_path / "f7_primary"
    prepare_f7_primary_benchmark(
        PROJECT_ROOT / "data/benchmark/f3_smoke",
        benchmark_dir,
        overwrite=False,
    )

    manifest = build_openai_primary_manifest(
        run_id="unit-f7-primary",
        benchmark_dir=benchmark_dir,
        price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
        budget_usd=Decimal("10.00"),
    )

    assert manifest.status is RunManifestStatus.FROZEN
    assert manifest.allow_paid is True
    assert manifest.non_reportable is False
    assert manifest.model.provider == "openai"
    assert manifest.model.id == "gpt-5.6-luna"
    assert manifest.model.reasoning_effort == "low"
    assert manifest.model.parameters == {"max_output_tokens": 1200}
    assert [split.value for split in manifest.splits] == [
        "test_public",
        "test_hidden",
        "multi_turn",
        "adversarial",
    ]
    assert manifest.repetitions == 3


def test_openai_cost_safe_manifest_binds_stability_design() -> None:
    manifest = build_openai_cost_safe_manifest(
        run_id="unit-f7-cost-safe",
        benchmark_dir=PROJECT_ROOT / "data/benchmark/f7_release_scale",
        price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
        budget_usd=Decimal("14.50"),
    )

    assert manifest.status is RunManifestStatus.FROZEN
    assert manifest.benchmark_version == "1.0.0-rc.2"
    assert manifest.budget_usd == Decimal("14.50")
    assert manifest.repetitions == 3
    assert manifest.execution_design is not None
    assert manifest.execution_design.primary_repetitions == 1
    assert manifest.execution_design.stability_additional_repetitions == 2
    assert len(manifest.execution_design.scientific_case_ids) == 1200
    assert len(manifest.execution_design.stability_subset_case_ids) == 150
    assert manifest.execution_design.stability_subset_sha256.startswith("sha256:")


def test_manifest_copy_detects_tampering(tmp_path: Path) -> None:
    manifest = build_fake_pilot_manifest(
        run_id="unit-fake-pilot",
        benchmark_dir=PROJECT_ROOT / "data/benchmark/f3_smoke",
        price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
    )
    source = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, source, overwrite=True)
    copy_manifest_for_run(source, run_dir)
    (run_dir / "run_manifest.json").write_text('{"schema_version":"1.0"}\n', encoding="utf-8")

    with pytest.raises(ProjectError, match="hash mismatch"):
        validate_manifest_copy(run_dir)


def test_manifest_copy_archives_explicit_superseded_manifest(tmp_path: Path) -> None:
    original = build_fake_pilot_manifest(
        run_id="unit-superseded",
        benchmark_dir=PROJECT_ROOT / "data/benchmark/f3_smoke",
        price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
    )
    original_path = tmp_path / "original.json"
    run_dir = tmp_path / "run"
    write_run_manifest(original, original_path, overwrite=True)
    copy_manifest_for_run(original_path, run_dir)
    original_hash = manifest_file_hash(original_path)
    replacement = original.model_copy(
        update={
            "code_commit": "b" * 40,
            "environment": {"supersedes_manifest_sha256": original_hash},
        }
    )
    replacement_path = tmp_path / "replacement.json"
    write_run_manifest(replacement, replacement_path, overwrite=True)

    copy_manifest_for_run(replacement_path, run_dir)

    assert manifest_file_hash(run_dir / "run_manifest.json") == manifest_file_hash(replacement_path)
    history_manifest = (
        run_dir / "manifest_history" / original_hash.removeprefix("sha256:") / "run_manifest.json"
    )
    assert history_manifest.is_file()
    assert manifest_file_hash(history_manifest) == original_hash


def test_manifest_copy_rejects_unrelated_manifest_mismatch(tmp_path: Path) -> None:
    original = build_fake_pilot_manifest(
        run_id="unit-mismatch-a",
        benchmark_dir=PROJECT_ROOT / "data/benchmark/f3_smoke",
        price_table_path=PROJECT_ROOT / "configs/pricing/openai_stale_example.json",
    )
    replacement = original.model_copy(update={"run_id": "unit-mismatch-b"})
    original_path = tmp_path / "original.json"
    replacement_path = tmp_path / "replacement.json"
    run_dir = tmp_path / "run"
    write_run_manifest(original, original_path, overwrite=True)
    write_run_manifest(replacement, replacement_path, overwrite=True)
    copy_manifest_for_run(original_path, run_dir)

    with pytest.raises(ProjectError, match="does not match"):
        copy_manifest_for_run(replacement_path, run_dir)
