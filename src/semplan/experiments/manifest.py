"""Experiment manifest creation, hashing, and local preflight validation."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.contracts import (
    Approach,
    BenchmarkManifest,
    DatasetSplit,
    ExecutionDesign,
    ExperimentMode,
    ExperimentModelConfig,
    PromptBinding,
    RunManifest,
    RunManifestStatus,
)
from semplan.data_generation.writer import canonical_json, sha256_file
from semplan.errors import ErrorCode, ProjectError
from semplan.experiments.design import build_cost_safe_execution_design
from semplan.prompts import PromptRegistry

DEFAULT_EXPERIMENT_CREATED_AT = datetime(2026, 8, 6, tzinfo=UTC)
DEFAULT_F6_SPLITS = [
    DatasetSplit.DEVELOPMENT,
    DatasetSplit.VALIDATION,
    DatasetSplit.TEST_PUBLIC,
    DatasetSplit.MULTI_TURN,
    DatasetSplit.ADVERSARIAL,
]
DEFAULT_F7_PRIMARY_SPLITS = [
    DatasetSplit.TEST_PUBLIC,
    DatasetSplit.TEST_HIDDEN,
    DatasetSplit.MULTI_TURN,
    DatasetSplit.ADVERSARIAL,
]
DEFAULT_APPROACHES = [Approach.A1, Approach.A2, Approach.A3, Approach.A4]


def load_run_manifest(path: Path) -> RunManifest:
    """Load a JSON or YAML run manifest through the strict contract."""

    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        payload = yaml.safe_load(raw)
    else:
        payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ProjectError(ErrorCode.CFG_INVALID, "Run manifest must be an object")
    return RunManifest.model_validate(payload)


def manifest_file_hash(path: Path) -> str:
    return f"sha256:{sha256_file(path)}"


def write_run_manifest(manifest: RunManifest, path: Path, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"Run manifest already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        canonical_json(manifest.model_dump(mode="json")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def copy_manifest_for_run(source: Path, output_dir: Path) -> tuple[Path, str]:
    """Copy the exact manifest bytes into the result directory and persist its hash."""

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "run_manifest.json"
    source_hash = manifest_file_hash(source)
    if target.exists():
        target_hash = manifest_file_hash(target)
        if target_hash != source_hash:
            source_manifest = load_run_manifest(source)
            if source_manifest.environment.get("supersedes_manifest_sha256") != target_hash:
                raise ProjectError(
                    ErrorCode.CFG_INVALID,
                    "Existing run manifest copy does not match requested manifest",
                    detail={"source_hash": source_hash, "target_hash": target_hash},
                )
            _archive_superseded_manifest(output_dir, target, target_hash)
            shutil.copyfile(source, target)
    else:
        shutil.copyfile(source, target)
    (output_dir / "run_manifest.sha256").write_text(source_hash + "\n", encoding="utf-8")
    return target, source_hash


def validate_manifest_copy(output_dir: Path) -> RunManifest:
    manifest_path = output_dir / "run_manifest.json"
    hash_path = output_dir / "run_manifest.sha256"
    if not manifest_path.exists() or not hash_path.exists():
        raise ProjectError(ErrorCode.CFG_INVALID, "Run manifest copy or hash is missing")
    expected = hash_path.read_text(encoding="utf-8").strip()
    actual = manifest_file_hash(manifest_path)
    if expected != actual:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Run manifest copy hash mismatch",
            detail={"expected": expected, "actual": actual},
        )
    return load_run_manifest(manifest_path)


def _archive_superseded_manifest(output_dir: Path, target: Path, target_hash: str) -> None:
    digest = target_hash.removeprefix("sha256:")
    history_dir = output_dir / "manifest_history" / digest
    history_dir.mkdir(parents=True, exist_ok=True)
    archived_manifest = history_dir / "run_manifest.json"
    archived_hash = history_dir / "run_manifest.sha256"
    if not archived_manifest.exists():
        shutil.copyfile(target, archived_manifest)
    archived_hash.write_text(target_hash + "\n", encoding="utf-8")


def build_fake_pilot_manifest(
    *,
    run_id: str,
    benchmark_dir: Path,
    price_table_path: Path,
    approaches: list[Approach] | None = None,
    splits: list[DatasetSplit] | None = None,
    repetitions: int = 1,
    randomization_seed: int = 20260806,
    budget_usd: Decimal = Decimal("0"),
    non_reportable: bool = True,
    created_at_utc: datetime = DEFAULT_EXPERIMENT_CREATED_AT,
) -> RunManifest:
    """Create a deterministic, non-paid pilot manifest from local artifacts."""

    benchmark_manifest_path = benchmark_dir / "benchmark_manifest.json"
    benchmark_manifest = BenchmarkManifest.model_validate_json(
        benchmark_manifest_path.read_text(encoding="utf-8")
    )
    catalog_sha256 = f"sha256:{load_catalog(Path('catalog')).sha256()}"
    prompt_registry = PromptRegistry.load(Path("prompts"))
    selected_approaches = approaches or DEFAULT_APPROACHES
    prompt_bindings = {
        approach: _prompt_binding(prompt_registry, approach) for approach in selected_approaches
    }
    return RunManifest(
        schema_version="1.0",
        run_id=run_id,
        status=RunManifestStatus.FROZEN,
        created_at_utc=created_at_utc,
        code_commit=_git_commit(),
        dirty_tree=_git_dirty(),
        non_reportable=non_reportable,
        dataset_version=benchmark_manifest.dataset_version,
        dataset_manifest_sha256=benchmark_manifest.dataset_manifest_hash,
        benchmark_manifest_sha256=manifest_file_hash(benchmark_manifest_path),
        catalog_sha256=catalog_sha256,
        approaches=selected_approaches,
        model=ExperimentModelConfig(
            provider="fake",
            id="fake-f6-pilot-v1",
            reasoning_effort="none",
            parameters={"temperature": "0", "max_output_tokens": 1200},
        ),
        prompts=prompt_bindings,
        splits=splits or DEFAULT_F6_SPLITS,
        repetitions=repetitions,
        randomization_seed=randomization_seed,
        budget_usd=budget_usd,
        price_table_sha256=manifest_file_hash(price_table_path),
        execution_policy_sha256=execution_policy_hash(),
        mode=ExperimentMode.PILOT,
        allow_paid=False,
    )


def build_openai_primary_manifest(
    *,
    run_id: str,
    benchmark_dir: Path,
    price_table_path: Path,
    budget_usd: Decimal,
    repetitions: int = 3,
    randomization_seed: int = 20260806,
    model_id: str = "gpt-5.6-luna",
    reasoning_effort: str = "low",
    max_output_tokens: int = 1200,
    created_at_utc: datetime = DEFAULT_EXPERIMENT_CREATED_AT,
    execution_design: ExecutionDesign | None = None,
) -> RunManifest:
    """Create the frozen reportable F7 primary OpenAI manifest without dispatching work."""

    benchmark_manifest_path = benchmark_dir / "benchmark_manifest.json"
    benchmark_manifest = BenchmarkManifest.model_validate_json(
        benchmark_manifest_path.read_text(encoding="utf-8")
    )
    if benchmark_manifest.state != "frozen":
        raise ProjectError(ErrorCode.CFG_INVALID, "F7 primary benchmark must be frozen")
    if not benchmark_manifest.hidden_included:
        raise ProjectError(ErrorCode.CFG_INVALID, "F7 primary benchmark must include hidden split")

    catalog_sha256 = f"sha256:{load_catalog(Path('catalog')).sha256()}"
    prompt_registry = PromptRegistry.load(Path("prompts"))
    prompt_bindings = {
        approach: _prompt_binding(prompt_registry, approach) for approach in DEFAULT_APPROACHES
    }
    return RunManifest(
        schema_version="1.0",
        run_id=run_id,
        status=RunManifestStatus.FROZEN,
        created_at_utc=created_at_utc,
        code_commit=_git_commit(),
        dirty_tree=_git_dirty(),
        non_reportable=False,
        benchmark_version=benchmark_manifest.benchmark_version,
        dataset_version=benchmark_manifest.dataset_version,
        dataset_manifest_sha256=benchmark_manifest.dataset_manifest_hash,
        benchmark_manifest_sha256=manifest_file_hash(benchmark_manifest_path),
        catalog_sha256=catalog_sha256,
        approaches=DEFAULT_APPROACHES,
        model=ExperimentModelConfig(
            provider="openai",
            id=model_id,
            reasoning_effort=reasoning_effort,
            parameters={"max_output_tokens": max_output_tokens},
        ),
        prompts=prompt_bindings,
        splits=DEFAULT_F7_PRIMARY_SPLITS,
        repetitions=repetitions,
        execution_design=execution_design,
        randomization_seed=randomization_seed,
        budget_usd=budget_usd,
        price_table_sha256=manifest_file_hash(price_table_path),
        execution_policy_sha256=execution_policy_hash(),
        mode=ExperimentMode.SYNCHRONOUS,
        allow_paid=True,
        environment=_environment_metadata(),
    )


def build_openai_cost_safe_manifest(
    *,
    run_id: str,
    benchmark_dir: Path,
    price_table_path: Path,
    budget_usd: Decimal,
    stability_case_count: int = 150,
    stability_seed: int = 20260811,
    primary_repetitions: int = 1,
    stability_additional_repetitions: int = 2,
    randomization_seed: int = 20260806,
    model_id: str = "gpt-5.6-luna",
    reasoning_effort: str = "low",
    max_output_tokens: int = 1200,
    created_at_utc: datetime = DEFAULT_EXPERIMENT_CREATED_AT,
) -> RunManifest:
    """Create the final F7 cost-safe manifest with a separate stability substudy."""

    execution_design = build_cost_safe_execution_design(
        load_benchmark_cases(benchmark_dir),
        stability_case_count=stability_case_count,
        stability_seed=stability_seed,
        primary_repetitions=primary_repetitions,
        stability_additional_repetitions=stability_additional_repetitions,
    )
    return build_openai_primary_manifest(
        run_id=run_id,
        benchmark_dir=benchmark_dir,
        price_table_path=price_table_path,
        budget_usd=budget_usd,
        repetitions=execution_design.max_repetitions,
        randomization_seed=randomization_seed,
        model_id=model_id,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        created_at_utc=created_at_utc,
        execution_design=execution_design,
    )


def validate_manifest_for_execution(
    manifest: RunManifest,
    *,
    benchmark_dir: Path,
    allow_paid: bool,
    dirty_tree: bool | None = None,
) -> dict[str, Any]:
    """Validate all local scientific bindings before a runner may dispatch work."""

    actual_dirty = _git_dirty() if dirty_tree is None else dirty_tree
    if manifest.status is RunManifestStatus.FROZEN and actual_dirty and not manifest.non_reportable:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Reportable frozen manifests cannot run from a dirty working tree",
            detail={"run_id": manifest.run_id},
        )
    if manifest.allow_paid and not allow_paid:
        raise ProjectError(
            ErrorCode.BUDGET_EXCEEDED,
            "Paid experiment execution requires --allow-paid",
            detail={"run_id": manifest.run_id},
        )

    benchmark_manifest_path = benchmark_dir / "benchmark_manifest.json"
    benchmark_manifest_hash = manifest_file_hash(benchmark_manifest_path)
    if benchmark_manifest_hash != manifest.benchmark_manifest_sha256:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Benchmark manifest hash does not match run manifest",
            detail={
                "expected": manifest.benchmark_manifest_sha256,
                "actual": benchmark_manifest_hash,
            },
        )
    benchmark_manifest = BenchmarkManifest.model_validate_json(
        benchmark_manifest_path.read_text(encoding="utf-8")
    )
    if benchmark_manifest.dataset_manifest_hash != manifest.dataset_manifest_sha256:
        raise ProjectError(ErrorCode.CFG_INVALID, "Dataset manifest hash mismatch")
    if benchmark_manifest.dataset_version != manifest.dataset_version:
        raise ProjectError(ErrorCode.CFG_INVALID, "Dataset version mismatch")
    if manifest.benchmark_version is not None and (
        benchmark_manifest.benchmark_version != manifest.benchmark_version
    ):
        raise ProjectError(ErrorCode.CFG_INVALID, "Benchmark version mismatch")
    if DatasetSplit.TEST_HIDDEN in manifest.splits and not benchmark_manifest.hidden_included:
        raise ProjectError(ErrorCode.CFG_INVALID, "Manifest requests hidden split not present")
    if manifest.execution_design is not None:
        cases = load_benchmark_cases(benchmark_dir)
        split_case_ids = sorted(case.case_id for case in cases if case.split in manifest.splits)
        design_case_ids = sorted(manifest.execution_design.scientific_case_ids)
        if split_case_ids != design_case_ids:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Execution design scientific case IDs do not match selected splits",
            )
        missing_subset = sorted(
            set(manifest.execution_design.stability_subset_case_ids).difference(split_case_ids)
        )
        if missing_subset:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Execution design references non-scientific stability case IDs",
                detail={"case_ids": missing_subset[:5]},
            )

    catalog_sha256 = f"sha256:{load_catalog(Path('catalog')).sha256()}"
    if catalog_sha256 != manifest.catalog_sha256:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Catalog hash does not match run manifest",
            detail={"expected": manifest.catalog_sha256, "actual": catalog_sha256},
        )

    prompt_registry = PromptRegistry.load(Path("prompts"))
    for approach in manifest.approaches:
        actual = _prompt_binding(prompt_registry, approach)
        expected = manifest.prompts[approach]
        if actual != expected:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Prompt binding does not match run manifest",
                detail={"approach": approach.value},
            )

    return {
        "benchmark_manifest_sha256": benchmark_manifest_hash,
        "catalog_sha256": catalog_sha256,
        "dirty_tree": actual_dirty,
        "non_reportable": manifest.non_reportable,
        "allow_paid": manifest.allow_paid,
    }


def execution_policy_hash() -> str:
    payload = {
        "a1": {
            "select_only_ast_validation": True,
            "read_only_transaction": True,
            "statement_timeout_ms": 5000,
            "row_cap": 1000,
        },
        "a2": {"max_tool_calls": 4, "dynamic_tools": False},
        "a3_a4": {"trusted_compiler": True, "model_sql_execution": False},
        "database": {"role": "semplan_readonly", "transaction": "read_only"},
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _prompt_binding(registry: PromptRegistry, approach: Approach) -> PromptBinding:
    prompt = registry.for_approach(approach)
    return PromptBinding(
        id=prompt.metadata.prompt_id,
        sha256=prompt.sha256,
        output_schema_ref=prompt.metadata.expected_output_schema,
        output_schema_sha256=prompt.output_schema_sha256,
    )


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _git_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    return bool(result.stdout.strip())


def _environment_metadata() -> dict[str, str]:
    return {
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "platform": platform.platform(),
    }
