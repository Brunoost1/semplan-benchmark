"""Small F0 command-line interface for local validation tasks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from semplan.benchmark import (
    approve_benchmark_reviews,
    generate_release_scale_benchmark,
    generate_smoke_benchmark,
    prepare_f7_primary_benchmark,
    release_target_matrix,
    validate_benchmark_dir,
    validate_benchmark_language_quality,
    validate_release_scale_benchmark,
)
from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.config import load_config_files
from semplan.contracts import Approach, RunManifest
from semplan.data_generation import (
    compare_dataset_dirs,
    generate_dataset,
    load_dataset,
    manifest_hash,
    validate_dataset_dir,
    write_dataset,
)
from semplan.e2e import run_free_e2e
from semplan.errors import ProjectError
from semplan.experiments import (
    build_fake_pilot_manifest,
    build_openai_cost_safe_manifest,
    build_openai_primary_manifest,
    create_superseding_hotfix_manifest,
    create_work_items,
    estimate_manifest_cost,
    load_run_manifest,
    manifest_file_hash,
    reconcile_partial_run_state,
    regenerate_paper_artifacts,
    run_experiment,
    validate_manifest_for_execution,
    validate_run_dir,
    write_run_manifest,
)
from semplan.experiments.design import validate_stability_execution_design
from semplan.smoke import run_manual_paid_smoke


def _validate_config(paths: Sequence[Path]) -> int:
    config = load_config_files(paths)
    print(json.dumps({"ok": True, "config_sha256": config.sha256()}, sort_keys=True))
    return 0


def _validate_catalog(path: Path) -> int:
    catalog = load_catalog(path)
    print(
        json.dumps(
            {
                "ok": True,
                "catalog_sha256": catalog.sha256(),
                "metrics": len(catalog.metrics),
                "dimensions": len(catalog.dimensions),
            },
            sort_keys=True,
        )
    )
    return 0


def _generate_data(profile: str, seed: int, output: Path, overwrite: bool) -> int:
    dataset = generate_dataset(profile, seed)
    write_dataset(dataset, output, overwrite=overwrite)
    validate_dataset_dir(output, write_report=True)
    print(
        json.dumps(
            {
                "ok": True,
                "dataset_dir": str(output),
                "manifest_hash": manifest_hash(output),
                "profile": profile,
                "seed": seed,
            },
            sort_keys=True,
        )
    )
    return 0


def _validate_data(dataset_dir: Path) -> int:
    report = validate_dataset_dir(dataset_dir, write_report=True)
    print(json.dumps({"ok": True, "status": report["status"]}, sort_keys=True))
    return 0


def _compare_data(left: Path, right: Path) -> int:
    result = compare_dataset_dirs(left, right)
    print(json.dumps({"ok": result["byte_equivalent"], **result}, sort_keys=True))
    return 0 if result["byte_equivalent"] else 1


def _load_data(dataset_dir: Path) -> int:
    loaded = load_dataset(dataset_dir)
    print(json.dumps({"ok": True, "loaded": loaded}, sort_keys=True))
    return 0


def _generate_benchmark(output: Path, dataset_dir: Path, overwrite: bool) -> int:
    manifest = generate_smoke_benchmark(output, dataset_dir, overwrite=overwrite)
    print(
        json.dumps(
            {
                "ok": True,
                "benchmark_dir": str(output),
                "case_count": manifest.case_count,
                "review_summary": {
                    status.value: count for status, count in manifest.review_summary.items()
                },
            },
            sort_keys=True,
        )
    )
    return 0


def _generate_release_benchmark(output: Path, dataset_dir: Path, overwrite: bool) -> int:
    manifest = generate_release_scale_benchmark(output, dataset_dir, overwrite=overwrite)
    print(
        json.dumps(
            {
                "ok": True,
                "benchmark_dir": str(output),
                "benchmark_version": manifest.benchmark_version,
                "case_count": manifest.case_count,
                "split_counts": {
                    split.value: count for split, count in manifest.split_counts.items()
                },
                "language_counts": {
                    language.value: count for language, count in manifest.language_counts.items()
                },
                "review_summary": {
                    status.value: count for status, count in manifest.review_summary.items()
                },
            },
            sort_keys=True,
        )
    )
    return 0


def _validate_benchmark(
    benchmark_dir: Path,
    require_approved: bool,
    execute_gold: bool,
    allow_hidden: bool,
) -> int:
    report = validate_benchmark_dir(
        benchmark_dir,
        require_approved=require_approved,
        execute_gold=execute_gold,
        allow_hidden=allow_hidden,
        write_report=True,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "status": report["status"],
                "case_count": report["case_count"],
                "review_counts": report["review_counts"],
            },
            sort_keys=True,
        )
    )
    return 0


def _validate_release_benchmark(benchmark_dir: Path) -> int:
    report = validate_release_scale_benchmark(benchmark_dir, write_report=True)
    print(
        json.dumps(
            {
                "ok": True,
                "status": report["status"],
                "case_count": report["case_count"],
                "counts": report["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


def _validate_language_quality(benchmark_dir: Path) -> int:
    report = validate_benchmark_language_quality(benchmark_dir, write_report=True)
    print(
        json.dumps(
            {
                "ok": True,
                "status": report["status"],
                "case_count": report["case_count"],
                "pt_br_case_count": report["pt_br_case_count"],
                "affected_pt_br_case_count": report["affected_pt_br_case_count"],
            },
            sort_keys=True,
        )
    )
    return 0


def _release_target_matrix() -> int:
    print(json.dumps({"ok": True, **release_target_matrix()}, sort_keys=True))
    return 0


def _prepare_f7_benchmark(source: Path, output: Path, overwrite: bool) -> int:
    result = prepare_f7_primary_benchmark(source, output, overwrite=overwrite)
    print(json.dumps(result, sort_keys=True))
    return 0


def _approve_benchmark(
    benchmark_dir: Path,
    reviewer: str,
    reviewed_at_raw: str,
    note: str,
    allow_hidden: bool,
) -> int:
    reviewed_at = _parse_datetime(reviewed_at_raw)
    result = approve_benchmark_reviews(
        benchmark_dir,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        note=note,
        allow_hidden=allow_hidden,
    )
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


def _e2e_free(benchmark_dir: Path, output_dir: Path) -> int:
    report = run_free_e2e(benchmark_dir=benchmark_dir, output_dir=output_dir)
    print(
        json.dumps(
            {
                "ok": True,
                "status": report["status"],
                "case_count": report["case_count"],
                "approaches": report["approaches"],
                "paid_api_calls": report["paid_api_calls"],
                "report_dir": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 0


def _paid_smoke(
    benchmark_dir: Path,
    case_id: str,
    approach: str,
    manifest: Path,
    price_table: Path,
    cache_dir: Path,
    output_dir: Path,
    allow_paid: bool,
    replay_only: bool,
) -> int:
    report = run_manual_paid_smoke(
        benchmark_dir=benchmark_dir,
        case_id=case_id,
        approach=Approach(approach),
        manifest_path=manifest,
        price_table_path=price_table,
        cache_dir=cache_dir,
        output_dir=output_dir,
        allow_paid=allow_paid,
        replay_only=replay_only,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "status": report["status"],
                "approach": report["approach"],
                "case_id": report["case_id"],
                "replay_only": report["replay_only"],
                "incremental_paid_calls": report["incremental_paid_calls"],
                "report": str(output_dir / "manual_paid_smoke_report.json"),
            },
            sort_keys=True,
        )
    )
    return 0


def _create_experiment_manifest(
    output: Path,
    benchmark_dir: Path,
    price_table: Path,
    run_id: str,
    repetitions: int,
    seed: int,
    budget_usd: str,
    non_reportable: bool,
    overwrite: bool,
) -> int:
    manifest = build_fake_pilot_manifest(
        run_id=run_id,
        benchmark_dir=benchmark_dir,
        price_table_path=price_table,
        repetitions=repetitions,
        randomization_seed=seed,
        budget_usd=Decimal(budget_usd),
        non_reportable=non_reportable,
    )
    write_run_manifest(manifest, output, overwrite=overwrite)
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(output),
                "run_id": manifest.run_id,
                "status": manifest.status.value,
                "non_reportable": manifest.non_reportable,
                "allow_paid": manifest.allow_paid,
            },
            sort_keys=True,
        )
    )
    return 0


def _create_openai_primary_manifest(
    output: Path,
    benchmark_dir: Path,
    price_table: Path,
    run_id: str,
    repetitions: int,
    seed: int,
    budget_usd: str,
    created_at_raw: str,
    overwrite: bool,
) -> int:
    manifest = build_openai_primary_manifest(
        run_id=run_id,
        benchmark_dir=benchmark_dir,
        price_table_path=price_table,
        budget_usd=Decimal(budget_usd),
        repetitions=repetitions,
        randomization_seed=seed,
        created_at_utc=_parse_datetime(created_at_raw),
    )
    write_run_manifest(manifest, output, overwrite=overwrite)
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(output),
                "run_id": manifest.run_id,
                "status": manifest.status.value,
                "non_reportable": manifest.non_reportable,
                "allow_paid": manifest.allow_paid,
                "dirty_tree": manifest.dirty_tree,
                "code_commit": manifest.code_commit,
            },
            sort_keys=True,
        )
    )
    return 0


def _create_openai_cost_safe_manifest(
    output: Path,
    benchmark_dir: Path,
    price_table: Path,
    run_id: str,
    seed: int,
    budget_usd: str,
    created_at_raw: str,
    stability_case_count: int,
    stability_seed: int,
    primary_repetitions: int,
    stability_additional_repetitions: int,
    overwrite: bool,
) -> int:
    manifest = build_openai_cost_safe_manifest(
        run_id=run_id,
        benchmark_dir=benchmark_dir,
        price_table_path=price_table,
        budget_usd=Decimal(budget_usd),
        randomization_seed=seed,
        created_at_utc=_parse_datetime(created_at_raw),
        stability_case_count=stability_case_count,
        stability_seed=stability_seed,
        primary_repetitions=primary_repetitions,
        stability_additional_repetitions=stability_additional_repetitions,
    )
    write_run_manifest(manifest, output, overwrite=overwrite)
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(output),
                "manifest_sha256": manifest_file_hash(output),
                "run_id": manifest.run_id,
                "status": manifest.status.value,
                "benchmark_version": manifest.benchmark_version,
                "benchmark_manifest_sha256": manifest.benchmark_manifest_sha256,
                "scientific_cases": len(manifest.execution_design.scientific_case_ids)
                if manifest.execution_design is not None
                else None,
                "stability_cases": len(manifest.execution_design.stability_subset_case_ids)
                if manifest.execution_design is not None
                else None,
                "repetitions": manifest.repetitions,
                "budget_usd": str(manifest.budget_usd),
                "dirty_tree": manifest.dirty_tree,
                "code_commit": manifest.code_commit,
            },
            sort_keys=True,
        )
    )
    return 0


def _validate_run_manifest(
    manifest_path: Path,
    benchmark_dir: Path,
    allow_paid: bool,
    expected_requests: int | None,
    expected_stability_cases: int | None,
) -> int:
    manifest = load_run_manifest(manifest_path)
    preflight = validate_manifest_for_execution(
        manifest,
        benchmark_dir=benchmark_dir,
        allow_paid=allow_paid,
    )
    stability = None
    if manifest.execution_design is not None:
        stability = validate_stability_execution_design(
            benchmark_dir=benchmark_dir,
            manifest_execution_design=manifest.execution_design,
            expected_stability_count=expected_stability_cases or 150,
        )
        if not stability["ok"]:
            raise ValueError(
                "Stability execution design validation failed: "
                + "; ".join(str(error) for error in stability["errors"])
            )
    request_count = _planned_request_count(manifest, benchmark_dir)
    if expected_requests is not None and request_count != expected_requests:
        raise ValueError(f"expected {expected_requests} planned requests, found {request_count}")
    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(manifest_path),
                "manifest_sha256": manifest_file_hash(manifest_path),
                "run_id": manifest.run_id,
                "request_count": request_count,
                "preflight": preflight,
                "stability": stability,
            },
            sort_keys=True,
        )
    )
    return 0


def _estimate_run_cost(
    manifest: Path,
    benchmark_dir: Path,
    price_table: Path,
    smoke_report: Path,
    previous_estimate: Path,
    output: Path,
    runtime_hard_stop_usd: str,
) -> int:
    report = estimate_manifest_cost(
        manifest_path=manifest,
        benchmark_dir=benchmark_dir,
        price_table_path=price_table,
        smoke_report_path=smoke_report,
        previous_estimate_path=previous_estimate,
        output_path=output,
        runtime_hard_stop_usd=Decimal(runtime_hard_stop_usd),
        created_at_utc="2026-08-11T18:40:00Z",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "estimate": str(output),
                "estimate_sha256": report["sha256"],
                "request_count": report["workload"]["request_count"],
                "unique_request_hash_count": report["workload"]["unique_request_hash_count"],
                "conservative_standard_with_1_20_safety_usd": report["costs"][
                    "conservative_standard_with_1_20_safety_usd"
                ],
                "runtime_hard_stop_usd": report["costs"]["runtime_hard_stop_usd"],
                "final_paid_execution_ready": report["readiness"]["final_paid_execution_ready"],
            },
            sort_keys=True,
        )
    )
    return 0


def _create_superseding_hotfix_manifest(
    original_manifest: Path,
    output: Path,
    code_commit: str,
    executable_code_commit: str,
    documentation_commit: str,
    created_at: str,
    reason: str,
    overwrite: bool,
) -> int:
    report = create_superseding_hotfix_manifest(
        original_manifest_path=original_manifest,
        output_path=output,
        code_commit=code_commit,
        executable_code_commit=executable_code_commit,
        documentation_commit=documentation_commit,
        created_at_utc=created_at,
        supersession_reason=reason,
        overwrite=overwrite,
    )
    print(json.dumps(report, sort_keys=True))
    return 0


def _reconcile_run_state(
    manifest: Path,
    benchmark_dir: Path,
    run_dir: Path,
    cache_dir: Path,
    output: Path,
) -> int:
    report = reconcile_partial_run_state(
        manifest_path=manifest,
        benchmark_dir=benchmark_dir,
        run_dir=run_dir,
        cache_dir=cache_dir,
        output_path=output,
    )
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "manifest_sha256": report["manifest_sha256"],
                "backup_dir": report["backup_dir"],
                "affected_count": len(report["affected"]),
                "before": report["before"],
                "after": report["after"],
            },
            sort_keys=True,
        )
    )
    return 0


def _planned_request_count(manifest: RunManifest, benchmark_dir: Path) -> int:
    execution_design = manifest.execution_design
    if execution_design is None:
        return len(create_work_items(manifest, load_benchmark_cases(benchmark_dir)))
    approaches = len(manifest.approaches)
    scientific = len(execution_design.scientific_case_ids)
    stability = len(execution_design.stability_subset_case_ids)
    return approaches * (
        scientific * execution_design.primary_repetitions
        + stability * execution_design.stability_additional_repetitions
    )


def _run_experiment(
    manifest: Path,
    benchmark_dir: Path,
    output_dir: Path,
    allow_paid: bool,
    resume: bool,
    max_items: int | None,
    price_table: Path | None,
    cache_dir: Path | None,
) -> int:
    summary = run_experiment(
        manifest_path=manifest,
        benchmark_dir=benchmark_dir,
        output_dir=output_dir,
        allow_paid=allow_paid,
        resume=resume,
        max_items=max_items,
        price_table_path=price_table,
        cache_dir=cache_dir,
    )
    print(json.dumps({"ok": True, **summary}, sort_keys=True))
    return 0


def _validate_experiment(run_dir: Path) -> int:
    report = validate_run_dir(run_dir)
    print(json.dumps(report, sort_keys=True))
    return 0


def _paper_artifacts(run_dir: Path, benchmark_dir: Path) -> int:
    report = regenerate_paper_artifacts(run_dir=run_dir, benchmark_dir=benchmark_dir)
    print(
        json.dumps(
            {
                "ok": True,
                "run_id": report["run_id"],
                "record_count": report["record_count"],
                "tables": len(report["tables"]),
                "figures": len(report["figures"]),
            },
            sort_keys=True,
        )
    )
    return 0


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("reviewed-at must include a timezone")
    return parsed.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="semplan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_config = subparsers.add_parser("validate-config")
    validate_config.add_argument("config", nargs="+", type=Path)

    validate_catalog = subparsers.add_parser("validate-catalog")
    validate_catalog.add_argument("catalog_root", type=Path)

    generate_data = subparsers.add_parser("generate-data")
    generate_data.add_argument("output", type=Path)
    generate_data.add_argument("--profile", choices=["small", "full"], default="small")
    generate_data.add_argument("--seed", type=int, default=20260806)
    generate_data.add_argument("--overwrite", action="store_true")

    validate_data = subparsers.add_parser("validate-data")
    validate_data.add_argument("dataset_dir", type=Path)

    compare_data = subparsers.add_parser("compare-data")
    compare_data.add_argument("left", type=Path)
    compare_data.add_argument("right", type=Path)

    load_data = subparsers.add_parser("load-data")
    load_data.add_argument("dataset_dir", type=Path)

    generate_benchmark = subparsers.add_parser("generate-benchmark")
    generate_benchmark.add_argument("output", type=Path)
    generate_benchmark.add_argument("--dataset-dir", type=Path, required=True)
    generate_benchmark.add_argument("--overwrite", action="store_true")

    generate_release = subparsers.add_parser("generate-release-benchmark")
    generate_release.add_argument("output", type=Path)
    generate_release.add_argument("--dataset-dir", type=Path, required=True)
    generate_release.add_argument("--overwrite", action="store_true")

    validate_benchmark = subparsers.add_parser("validate-benchmark")
    validate_benchmark.add_argument("benchmark_dir", type=Path)
    validate_benchmark.add_argument("--require-approved", action="store_true")
    validate_benchmark.add_argument("--execute-gold", action="store_true")
    validate_benchmark.add_argument("--allow-hidden", action="store_true")

    validate_release = subparsers.add_parser("validate-release-benchmark")
    validate_release.add_argument("benchmark_dir", type=Path)

    validate_language = subparsers.add_parser("validate-language-quality")
    validate_language.add_argument("benchmark_dir", type=Path)

    subparsers.add_parser("release-target-matrix")

    prepare_f7 = subparsers.add_parser("prepare-f7-benchmark")
    prepare_f7.add_argument("output", type=Path)
    prepare_f7.add_argument("--source", type=Path, default=Path("data/benchmark/f3_smoke"))
    prepare_f7.add_argument("--overwrite", action="store_true")

    approve_benchmark = subparsers.add_parser("approve-benchmark")
    approve_benchmark.add_argument("benchmark_dir", type=Path)
    approve_benchmark.add_argument("--reviewer", required=True)
    approve_benchmark.add_argument("--reviewed-at", required=True)
    approve_benchmark.add_argument("--note", required=True)
    approve_benchmark.add_argument("--allow-hidden", action="store_true")

    e2e_free = subparsers.add_parser("e2e-free")
    e2e_free.add_argument("--benchmark-dir", type=Path, default=Path("data/benchmark/f3_smoke"))
    e2e_free.add_argument("--output-dir", type=Path, default=Path("artifacts/runs/free_e2e"))

    paid_smoke = subparsers.add_parser("paid-smoke")
    paid_smoke.add_argument("--benchmark-dir", type=Path, default=Path("data/benchmark/f3_smoke"))
    paid_smoke.add_argument("--case-id", required=True)
    paid_smoke.add_argument(
        "--approach", choices=[approach.value for approach in Approach], required=True
    )
    paid_smoke.add_argument("--manifest", type=Path, required=True)
    paid_smoke.add_argument("--price-table", type=Path, required=True)
    paid_smoke.add_argument("--cache-dir", type=Path, default=Path("artifacts/provider_cache"))
    paid_smoke.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/runs/manual_paid_smoke")
    )
    paid_smoke.add_argument("--allow-paid", action="store_true")
    paid_smoke.add_argument("--replay-only", action="store_true")

    create_manifest = subparsers.add_parser("create-experiment-manifest")
    create_manifest.add_argument("output", type=Path)
    create_manifest.add_argument(
        "--benchmark-dir", type=Path, default=Path("data/benchmark/f3_smoke")
    )
    create_manifest.add_argument(
        "--price-table",
        type=Path,
        default=Path("configs/pricing/openai_stale_example.json"),
    )
    create_manifest.add_argument("--run-id", default="f6-fake-pilot-20260806")
    create_manifest.add_argument("--repetitions", type=int, default=1)
    create_manifest.add_argument("--seed", type=int, default=20260806)
    create_manifest.add_argument("--budget-usd", default="0")
    create_manifest.add_argument("--non-reportable", action="store_true")
    create_manifest.add_argument("--overwrite", action="store_true")

    openai_manifest = subparsers.add_parser("create-openai-primary-manifest")
    openai_manifest.add_argument("output", type=Path)
    openai_manifest.add_argument(
        "--benchmark-dir", type=Path, default=Path("data/benchmark/f7_primary")
    )
    openai_manifest.add_argument("--price-table", type=Path, required=True)
    openai_manifest.add_argument("--run-id", default="f7-primary-20260811")
    openai_manifest.add_argument("--repetitions", type=int, default=3)
    openai_manifest.add_argument("--seed", type=int, default=20260806)
    openai_manifest.add_argument("--budget-usd", default="10.00")
    openai_manifest.add_argument("--created-at", default="2026-08-11T12:00:00Z")
    openai_manifest.add_argument("--overwrite", action="store_true")

    cost_safe_manifest = subparsers.add_parser("create-openai-cost-safe-manifest")
    cost_safe_manifest.add_argument("output", type=Path)
    cost_safe_manifest.add_argument(
        "--benchmark-dir", type=Path, default=Path("data/benchmark/f7_release_scale")
    )
    cost_safe_manifest.add_argument("--price-table", type=Path, required=True)
    cost_safe_manifest.add_argument("--run-id", default="f7-final-cost-safe-20260811")
    cost_safe_manifest.add_argument("--seed", type=int, default=20260806)
    cost_safe_manifest.add_argument("--budget-usd", default="14.50")
    cost_safe_manifest.add_argument("--created-at", default="2026-08-11T18:35:00Z")
    cost_safe_manifest.add_argument("--stability-case-count", type=int, default=150)
    cost_safe_manifest.add_argument("--stability-seed", type=int, default=20260811)
    cost_safe_manifest.add_argument("--primary-repetitions", type=int, default=1)
    cost_safe_manifest.add_argument("--stability-additional-repetitions", type=int, default=2)
    cost_safe_manifest.add_argument("--overwrite", action="store_true")

    validate_manifest = subparsers.add_parser("validate-run-manifest")
    validate_manifest.add_argument("manifest", type=Path)
    validate_manifest.add_argument(
        "--benchmark-dir", type=Path, default=Path("data/benchmark/f7_release_scale")
    )
    validate_manifest.add_argument("--allow-paid", action="store_true")
    validate_manifest.add_argument("--expected-requests", type=int, default=None)
    validate_manifest.add_argument("--expected-stability-cases", type=int, default=None)

    estimate_run_cost = subparsers.add_parser("estimate-run-cost")
    estimate_run_cost.add_argument("manifest", type=Path)
    estimate_run_cost.add_argument(
        "--benchmark-dir", type=Path, default=Path("data/benchmark/f7_release_scale")
    )
    estimate_run_cost.add_argument("--price-table", type=Path, required=True)
    estimate_run_cost.add_argument("--smoke-report", type=Path, required=True)
    estimate_run_cost.add_argument("--previous-estimate", type=Path, required=True)
    estimate_run_cost.add_argument("--output", type=Path, required=True)
    estimate_run_cost.add_argument("--runtime-hard-stop-usd", default="14.50")

    supersede_manifest = subparsers.add_parser("create-superseding-hotfix-manifest")
    supersede_manifest.add_argument("original_manifest", type=Path)
    supersede_manifest.add_argument("output", type=Path)
    supersede_manifest.add_argument("--code-commit", required=True)
    supersede_manifest.add_argument("--executable-code-commit", required=True)
    supersede_manifest.add_argument("--documentation-commit", required=True)
    supersede_manifest.add_argument("--created-at", default="2026-08-11T19:45:00Z")
    supersede_manifest.add_argument(
        "--reason",
        default="local execution-capture hotfix for DBAPI failures",
    )
    supersede_manifest.add_argument("--overwrite", action="store_true")

    reconcile_run = subparsers.add_parser("reconcile-run-state")
    reconcile_run.add_argument("manifest", type=Path)
    reconcile_run.add_argument("--benchmark-dir", type=Path, required=True)
    reconcile_run.add_argument("--run-dir", type=Path, required=True)
    reconcile_run.add_argument("--cache-dir", type=Path, required=True)
    reconcile_run.add_argument("--output", type=Path, required=True)

    run_experiment_parser = subparsers.add_parser("run-experiment")
    run_experiment_parser.add_argument("manifest", type=Path)
    run_experiment_parser.add_argument(
        "--benchmark-dir", type=Path, default=Path("data/benchmark/f3_smoke")
    )
    run_experiment_parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/runs/f6_fake_pilot")
    )
    run_experiment_parser.add_argument("--allow-paid", action="store_true")
    run_experiment_parser.add_argument("--resume", action="store_true")
    run_experiment_parser.add_argument("--max-items", type=int, default=None)
    run_experiment_parser.add_argument("--price-table", type=Path, default=None)
    run_experiment_parser.add_argument("--cache-dir", type=Path, default=None)

    validate_experiment = subparsers.add_parser("validate-experiment")
    validate_experiment.add_argument("run_dir", type=Path)

    paper_artifacts = subparsers.add_parser("paper-artifacts")
    paper_artifacts.add_argument("run_dir", type=Path, default=Path("artifacts/runs/f6_fake_pilot"))
    paper_artifacts.add_argument(
        "--benchmark-dir", type=Path, default=Path("data/benchmark/f3_smoke")
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "validate-config":
            return _validate_config(args.config)
        if args.command == "validate-catalog":
            return _validate_catalog(args.catalog_root)
        if args.command == "generate-data":
            return _generate_data(args.profile, args.seed, args.output, args.overwrite)
        if args.command == "validate-data":
            return _validate_data(args.dataset_dir)
        if args.command == "compare-data":
            return _compare_data(args.left, args.right)
        if args.command == "load-data":
            return _load_data(args.dataset_dir)
        if args.command == "generate-benchmark":
            return _generate_benchmark(args.output, args.dataset_dir, args.overwrite)
        if args.command == "generate-release-benchmark":
            return _generate_release_benchmark(args.output, args.dataset_dir, args.overwrite)
        if args.command == "validate-benchmark":
            return _validate_benchmark(
                args.benchmark_dir,
                args.require_approved,
                args.execute_gold,
                args.allow_hidden,
            )
        if args.command == "validate-release-benchmark":
            return _validate_release_benchmark(args.benchmark_dir)
        if args.command == "validate-language-quality":
            return _validate_language_quality(args.benchmark_dir)
        if args.command == "release-target-matrix":
            return _release_target_matrix()
        if args.command == "prepare-f7-benchmark":
            return _prepare_f7_benchmark(args.source, args.output, args.overwrite)
        if args.command == "e2e-free":
            return _e2e_free(args.benchmark_dir, args.output_dir)
        if args.command == "paid-smoke":
            return _paid_smoke(
                args.benchmark_dir,
                args.case_id,
                args.approach,
                args.manifest,
                args.price_table,
                args.cache_dir,
                args.output_dir,
                args.allow_paid,
                args.replay_only,
            )
        if args.command == "create-experiment-manifest":
            return _create_experiment_manifest(
                args.output,
                args.benchmark_dir,
                args.price_table,
                args.run_id,
                args.repetitions,
                args.seed,
                args.budget_usd,
                args.non_reportable,
                args.overwrite,
            )
        if args.command == "create-openai-primary-manifest":
            return _create_openai_primary_manifest(
                args.output,
                args.benchmark_dir,
                args.price_table,
                args.run_id,
                args.repetitions,
                args.seed,
                args.budget_usd,
                args.created_at,
                args.overwrite,
            )
        if args.command == "create-openai-cost-safe-manifest":
            return _create_openai_cost_safe_manifest(
                args.output,
                args.benchmark_dir,
                args.price_table,
                args.run_id,
                args.seed,
                args.budget_usd,
                args.created_at,
                args.stability_case_count,
                args.stability_seed,
                args.primary_repetitions,
                args.stability_additional_repetitions,
                args.overwrite,
            )
        if args.command == "validate-run-manifest":
            return _validate_run_manifest(
                args.manifest,
                args.benchmark_dir,
                args.allow_paid,
                args.expected_requests,
                args.expected_stability_cases,
            )
        if args.command == "estimate-run-cost":
            return _estimate_run_cost(
                args.manifest,
                args.benchmark_dir,
                args.price_table,
                args.smoke_report,
                args.previous_estimate,
                args.output,
                args.runtime_hard_stop_usd,
            )
        if args.command == "create-superseding-hotfix-manifest":
            return _create_superseding_hotfix_manifest(
                args.original_manifest,
                args.output,
                args.code_commit,
                args.executable_code_commit,
                args.documentation_commit,
                args.created_at,
                args.reason,
                args.overwrite,
            )
        if args.command == "reconcile-run-state":
            return _reconcile_run_state(
                args.manifest,
                args.benchmark_dir,
                args.run_dir,
                args.cache_dir,
                args.output,
            )
        if args.command == "run-experiment":
            return _run_experiment(
                args.manifest,
                args.benchmark_dir,
                args.output_dir,
                args.allow_paid,
                args.resume,
                args.max_items,
                args.price_table,
                args.cache_dir,
            )
        if args.command == "validate-experiment":
            return _validate_experiment(args.run_dir)
        if args.command == "paper-artifacts":
            return _paper_artifacts(args.run_dir, args.benchmark_dir)
        return _approve_benchmark(
            args.benchmark_dir,
            args.reviewer,
            args.reviewed_at,
            args.note,
            args.allow_hidden,
        )
    except ProjectError as exc:
        print(exc.to_record().model_dump_json())
        return 2
    except (FileExistsError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
