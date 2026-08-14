from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from semplan.cli.main import main
from semplan.contracts import ReviewStatus

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_validate_config_cli_succeeds(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main(["validate-config", str(PROJECT_ROOT / "configs/base.yaml")])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"ok": true' in captured.out
    assert "config_sha256" in captured.out


def test_validate_catalog_cli_succeeds(capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main(["validate-catalog", str(PROJECT_ROOT / "catalog")])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"ok": true' in captured.out
    assert '"metrics": 12' in captured.out


def test_validate_config_cli_returns_error_for_missing_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    exit_code = main(["validate-config", str(tmp_path / "missing.yaml")])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "CFG_INVALID" in captured.out


def test_generate_validate_compare_data_cli_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "dataset"
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "semplan.cli.main.generate_dataset",
        lambda profile, seed: {"profile": profile, "seed": seed},
    )
    monkeypatch.setattr(
        "semplan.cli.main.write_dataset",
        lambda dataset, target, overwrite: calls.append(("write", (dataset, target, overwrite))),
    )
    monkeypatch.setattr(
        "semplan.cli.main.validate_dataset_dir",
        lambda dataset_dir, write_report: {"status": "passed", "dataset": str(dataset_dir)},
    )
    monkeypatch.setattr(
        "semplan.cli.main.manifest_hash", lambda dataset_dir: "sha256:" + ("a" * 64)
    )
    monkeypatch.setattr(
        "semplan.cli.main.compare_dataset_dirs",
        lambda left, right: {"byte_equivalent": True, "left": str(left), "right": str(right)},
    )

    assert main(["generate-data", str(output), "--seed", "7", "--overwrite"]) == 0
    assert main(["validate-data", str(output)]) == 0
    assert main(["compare-data", str(output), str(output)]) == 0

    captured = capsys.readouterr()
    assert captured.out.count('"ok": true') == 3
    assert calls


def test_compare_data_cli_returns_one_for_difference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "semplan.cli.main.compare_dataset_dirs",
        lambda left, right: {"byte_equivalent": False, "left": str(left), "right": str(right)},
    )

    exit_code = main(["compare-data", str(tmp_path / "a"), str(tmp_path / "b")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert '"ok": false' in captured.out


def test_load_data_cli_dispatches_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("semplan.cli.main.load_dataset", lambda dataset_dir: {"orders": 3})

    assert main(["load-data", str(tmp_path / "dataset")]) == 0

    captured = capsys.readouterr()
    assert '"orders": 3' in captured.out


def test_generate_validate_and_approve_benchmark_cli_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    benchmark_dir = tmp_path / "benchmark"
    dataset_dir = tmp_path / "dataset"
    manifest = SimpleNamespace(
        case_count=50,
        review_summary={ReviewStatus.PENDING_AUTHOR_REVIEW: 100},
    )
    monkeypatch.setattr(
        "semplan.cli.main.generate_smoke_benchmark", lambda *args, **kwargs: manifest
    )
    monkeypatch.setattr(
        "semplan.cli.main.generate_release_scale_benchmark",
        lambda *args, **kwargs: SimpleNamespace(
            benchmark_version="1.0.0-rc.2",
            case_count=1800,
            split_counts={},
            language_counts={},
            review_summary={ReviewStatus.PENDING_AUTHOR_REVIEW: 3600},
        ),
    )
    monkeypatch.setattr(
        "semplan.cli.main.validate_benchmark_dir",
        lambda *args, **kwargs: {
            "status": "passed",
            "case_count": 50,
            "review_counts": {"approved": 100},
        },
    )
    monkeypatch.setattr(
        "semplan.cli.main.validate_release_scale_benchmark",
        lambda *args, **kwargs: {
            "status": "passed",
            "case_count": 1800,
            "counts": {"case_count": 1800},
        },
    )
    monkeypatch.setattr(
        "semplan.cli.main.validate_benchmark_language_quality",
        lambda *args, **kwargs: {
            "status": "passed",
            "case_count": 1800,
            "pt_br_case_count": 900,
            "affected_pt_br_case_count": 0,
        },
    )
    approve_calls: list[dict[str, object]] = []

    def fake_approve_benchmark_reviews(*args: object, **kwargs: object) -> dict[str, object]:
        approve_calls.append(kwargs)
        return {
            "benchmark_manifest_sha256": "sha256:" + ("b" * 64),
            "case_count": 50,
            "review_counts": {"approved": 100},
        }

    monkeypatch.setattr(
        "semplan.cli.main.approve_benchmark_reviews", fake_approve_benchmark_reviews
    )
    monkeypatch.setattr(
        "semplan.cli.main.prepare_f7_primary_benchmark",
        lambda *args, **kwargs: {
            "ok": True,
            "benchmark_dir": str(benchmark_dir),
            "case_count": 49,
            "scientific_case_count": 17,
            "split_counts": {"test_hidden": 8},
            "hidden_families": [],
            "excluded_case_ids": ["ADV-SMK-000001"],
            "validation_status": "passed",
            "leakage_status": "passed",
        },
    )

    assert main(["generate-benchmark", str(benchmark_dir), "--dataset-dir", str(dataset_dir)]) == 0
    assert (
        main(
            [
                "generate-release-benchmark",
                str(benchmark_dir),
                "--dataset-dir",
                str(dataset_dir),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "validate-benchmark",
                str(benchmark_dir),
                "--require-approved",
                "--execute-gold",
                "--allow-hidden",
            ]
        )
        == 0
    )
    assert main(["validate-release-benchmark", str(benchmark_dir)]) == 0
    assert main(["validate-language-quality", str(benchmark_dir)]) == 0
    assert main(["release-target-matrix"]) == 0
    assert main(["prepare-f7-benchmark", str(benchmark_dir), "--overwrite"]) == 0
    assert (
        main(
            [
                "approve-benchmark",
                str(benchmark_dir),
                "--reviewer",
                "Reviewer",
                "--reviewed-at",
                "2026-08-06T12:00:00Z",
                "--note",
                "Approved.",
                "--allow-hidden",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.count('"ok": true') == 8
    assert approve_calls[0]["allow_hidden"] is True


def test_e2e_free_cli_dispatches_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "semplan.cli.main.run_free_e2e",
        lambda benchmark_dir, output_dir: {
            "status": "passed",
            "case_count": 50,
            "approaches": ["A3", "A4"],
            "paid_api_calls": 0,
        },
    )

    assert (
        main(
            [
                "e2e-free",
                "--benchmark-dir",
                str(tmp_path / "benchmark"),
                "--output-dir",
                str(tmp_path / "e2e"),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert '"paid_api_calls": 0' in captured.out


def test_paid_smoke_cli_dispatches_manual_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "semplan.cli.main.run_manual_paid_smoke",
        lambda **kwargs: {
            "status": "passed",
            "approach": kwargs["approach"].value,
            "case_id": kwargs["case_id"],
            "replay_only": kwargs["replay_only"],
            "incremental_paid_calls": 0,
        },
    )

    assert (
        main(
            [
                "paid-smoke",
                "--case-id",
                "DEV-SMK-000001",
                "--approach",
                "A3",
                "--manifest",
                str(tmp_path / "manifest.json"),
                "--price-table",
                str(tmp_path / "prices.json"),
                "--replay-only",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert '"incremental_paid_calls": 0' in captured.out


def test_experiment_cli_dispatches_manifest_run_validation_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = SimpleNamespace(
        run_id="f6-fake-pilot",
        status=SimpleNamespace(value="frozen"),
        non_reportable=True,
        allow_paid=False,
        dirty_tree=False,
        code_commit="a" * 40,
    )
    openai_manifest = SimpleNamespace(
        run_id="f7-primary-20260811",
        status=SimpleNamespace(value="frozen"),
        non_reportable=False,
        allow_paid=True,
        dirty_tree=False,
        code_commit="b" * 40,
    )
    writes: list[Path] = []
    monkeypatch.setattr("semplan.cli.main.build_fake_pilot_manifest", lambda **kwargs: manifest)
    monkeypatch.setattr(
        "semplan.cli.main.build_openai_primary_manifest", lambda **kwargs: openai_manifest
    )
    monkeypatch.setattr(
        "semplan.cli.main.write_run_manifest",
        lambda manifest, output, overwrite: writes.append(output),
    )
    monkeypatch.setattr(
        "semplan.cli.main.run_experiment",
        lambda **kwargs: {
            "schema_version": "1.0",
            "run_id": "f6-fake-pilot",
            "status": "completed",
            "work_item_count": 2,
            "result_record_count": 2,
        },
    )
    monkeypatch.setattr(
        "semplan.cli.main.validate_run_dir",
        lambda run_dir: {"ok": True, "run_id": "f6-fake-pilot", "record_count": 2},
    )
    monkeypatch.setattr(
        "semplan.cli.main.regenerate_paper_artifacts",
        lambda run_dir, benchmark_dir: {
            "run_id": "f6-fake-pilot",
            "record_count": 2,
            "tables": {"a": {}},
            "figures": {"b": {}},
        },
    )

    assert (
        main(
            [
                "create-experiment-manifest",
                str(tmp_path / "manifest.json"),
                "--non-reportable",
                "--overwrite",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "create-openai-primary-manifest",
                str(tmp_path / "openai_manifest.json"),
                "--price-table",
                str(tmp_path / "prices.json"),
                "--created-at",
                "2026-08-11T12:00:00Z",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "run-experiment",
                str(tmp_path / "manifest.json"),
                "--output-dir",
                str(tmp_path / "run"),
                "--resume",
            ]
        )
        == 0
    )
    assert main(["validate-experiment", str(tmp_path / "run")]) == 0
    assert main(["paper-artifacts", str(tmp_path / "run")]) == 0

    captured = capsys.readouterr()
    assert writes == [tmp_path / "manifest.json", tmp_path / "openai_manifest.json"]
    assert captured.out.count('"ok": true') == 5
    assert '"figures": 1' in captured.out


def test_cost_safe_manifest_cli_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    design = SimpleNamespace(
        design_id="f7-primary-plus-stability-v1",
        scientific_case_ids=["TST-PUB-REL-000001"] * 1200,
        stability_subset_case_ids=["TST-PUB-REL-000001"] * 150,
        primary_repetitions=1,
        stability_additional_repetitions=2,
    )
    manifest = SimpleNamespace(
        run_id="f7-final-cost-safe-20260811",
        status=SimpleNamespace(value="frozen"),
        benchmark_version="1.0.0-rc.2",
        benchmark_manifest_sha256="sha256:" + ("a" * 64),
        execution_design=design,
        repetitions=3,
        budget_usd="14.50",
        dirty_tree=False,
        code_commit="b" * 40,
        approaches=[
            SimpleNamespace(value="A1"),
            SimpleNamespace(value="A2"),
            SimpleNamespace(value="A3"),
            SimpleNamespace(value="A4"),
        ],
    )
    monkeypatch.setattr(
        "semplan.cli.main.build_openai_cost_safe_manifest",
        lambda **kwargs: manifest,
    )
    monkeypatch.setattr("semplan.cli.main.write_run_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr("semplan.cli.main.manifest_file_hash", lambda path: "sha256:" + ("c" * 64))
    monkeypatch.setattr("semplan.cli.main.load_run_manifest", lambda path: manifest)
    monkeypatch.setattr(
        "semplan.cli.main.validate_manifest_for_execution",
        lambda *args, **kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        "semplan.cli.main.validate_stability_execution_design",
        lambda **kwargs: {"ok": True, "status": "passed"},
    )
    monkeypatch.setattr(
        "semplan.cli.main.estimate_manifest_cost",
        lambda **kwargs: {
            "sha256": "sha256:" + ("d" * 64),
            "workload": {"request_count": 6000, "unique_request_hash_count": 6000},
            "costs": {
                "conservative_standard_with_1_20_safety_usd": "13.337771",
                "runtime_hard_stop_usd": "14.50",
            },
            "readiness": {"final_paid_execution_ready": True},
        },
    )

    assert (
        main(
            [
                "create-openai-cost-safe-manifest",
                str(tmp_path / "run_manifest.json"),
                "--price-table",
                str(tmp_path / "prices.json"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "validate-run-manifest",
                str(tmp_path / "run_manifest.json"),
                "--allow-paid",
                "--expected-requests",
                "6000",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "estimate-run-cost",
                str(tmp_path / "run_manifest.json"),
                "--price-table",
                str(tmp_path / "prices.json"),
                "--smoke-report",
                str(tmp_path / "smoke.json"),
                "--previous-estimate",
                str(tmp_path / "estimate.json"),
                "--output",
                str(tmp_path / "cost.json"),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 3
    assert '"request_count": 6000' in captured.out


def test_cli_converts_runtime_and_value_errors_to_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "semplan.cli.main.run_free_e2e",
        lambda benchmark_dir, output_dir: (_ for _ in ()).throw(RuntimeError("not ready")),
    )

    runtime_exit = main(["e2e-free", "--benchmark-dir", str(tmp_path / "benchmark")])
    value_exit = main(
        [
            "approve-benchmark",
            str(tmp_path / "benchmark"),
            "--reviewer",
            "Reviewer",
            "--reviewed-at",
            datetime(2026, 8, 6, 12, 0, 0).isoformat(),
            "--note",
            "Approved.",
        ]
    )

    captured = capsys.readouterr()
    assert runtime_exit == 2
    assert value_exit == 2
    assert "not ready" in captured.out
    assert "timezone" in captured.out
