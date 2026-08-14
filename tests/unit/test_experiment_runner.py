from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from semplan.approaches.semantic_plan import fixture_payloads_from_benchmark
from semplan.benchmark.validator import load_benchmark_cases
from semplan.contracts import (
    AnalysisRole,
    Approach,
    CostEstimate,
    DatasetSplit,
    ExperimentMode,
    ExperimentModelConfig,
    ModelPricing,
    PriceTable,
    ProviderFinishStatus,
    ProviderResponse,
    ProviderUsage,
)
from semplan.errors import ErrorCode, ProjectError
from semplan.experiments.manifest import (
    build_fake_pilot_manifest,
    build_openai_cost_safe_manifest,
    write_run_manifest,
)
from semplan.experiments.recovery import _planned_requests, reconcile_partial_run_state
from semplan.experiments.runner import create_work_items, run_experiment, validate_run_dir
from semplan.providers import FakeProvider, ProviderCache

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"
PRICE_TABLE = PROJECT_ROOT / "configs/pricing/openai_stale_example.json"


def test_work_item_order_is_seeded_and_paired() -> None:
    cases = load_benchmark_cases(BENCHMARK_DIR)[:6]
    manifest = build_fake_pilot_manifest(
        run_id="unit-work-order",
        benchmark_dir=BENCHMARK_DIR,
        price_table_path=PRICE_TABLE,
    ).model_copy(update={"approaches": [Approach.A1, Approach.A3], "repetitions": 2})

    left = create_work_items(manifest, cases)
    right = create_work_items(manifest, cases)

    assert left == right
    assert len(left) == len(cases) * 2 * 2
    assert left[0].case_id == left[1].case_id
    assert {left[0].approach, left[1].approach} == {Approach.A1, Approach.A3}


def test_interrupted_fake_run_resumes_without_duplicate_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_manifest = build_fake_pilot_manifest(
        run_id="unit-resume",
        benchmark_dir=BENCHMARK_DIR,
        price_table_path=PRICE_TABLE,
    )
    manifest = base_manifest.model_copy(
        update={
            "approaches": [Approach.A3],
            "prompts": {Approach.A3: base_manifest.prompts[Approach.A3]},
            "splits": [DatasetSplit.ADVERSARIAL],
        }
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    calls = 0
    original_complete = FakeProvider.complete

    def counting_complete(self: FakeProvider, request):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original_complete(self, request)

    monkeypatch.setattr(FakeProvider, "complete", counting_complete)

    first = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=1,
    )
    second = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
    )

    assert first["status"] == "interrupted"
    assert second["status"] == "completed"
    assert calls == 2
    assert validate_run_dir(run_dir)["record_count"] == 2


def test_validate_run_dir_rejects_artifact_hash_mismatch(tmp_path: Path) -> None:
    base_manifest = build_fake_pilot_manifest(
        run_id="unit-tamper",
        benchmark_dir=BENCHMARK_DIR,
        price_table_path=PRICE_TABLE,
    )
    manifest = base_manifest.model_copy(
        update={
            "approaches": [Approach.A3],
            "prompts": {Approach.A3: base_manifest.prompts[Approach.A3]},
            "splits": [DatasetSplit.ADVERSARIAL],
        }
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)
    run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
    )
    raw_file = next((run_dir / "raw").glob("*.json"))
    raw_file.write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(ProjectError, match="validation failed"):
        validate_run_dir(run_dir)


def test_dry_run_materializes_ledger_without_provider_calls(tmp_path: Path) -> None:
    manifest = _adversarial_a3_manifest("unit-dry-run").model_copy(
        update={"mode": ExperimentMode.DRY_RUN}
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    summary = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
    )

    assert summary["status"] == "dry_run"
    assert summary["result_record_count"] == 0
    assert (run_dir / "work_ledger.json").is_file()


def test_runner_uses_manifest_model_parameters_in_provider_request(tmp_path: Path) -> None:
    manifest = _adversarial_a3_manifest("unit-manifest-params").model_copy(
        update={
            "model": ExperimentModelConfig(
                provider="fake",
                id="fake-model",
                reasoning_effort="none",
                parameters={"temperature": "0", "max_output_tokens": 77},
            )
        }
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=1,
    )

    request_path = next((run_dir / "rendered_prompts").glob("*.json"))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["model"] == "fake-model"
    assert request["inference_parameters"] == {
        "max_output_tokens": 77,
        "temperature": "0",
    }
    assert request["metadata"]["run_id"] == "unit-manifest-params"
    assert request["metadata"]["repetition"] == "1"
    assert request["metadata"]["work_item_id"].startswith("sha256:")


def test_planned_repetitions_have_distinct_provider_request_hashes(tmp_path: Path) -> None:
    manifest = _adversarial_a3_manifest("unit-repetition-hashes").model_copy(
        update={"repetitions": 2}
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=3,
    )

    requests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "rendered_prompts").glob("*.json"))
    ]
    request_hashes = {request["idempotency_hash"] for request in requests}
    repetition_keys = {
        (request["metadata"]["case_id"], request["metadata"]["repetition"]) for request in requests
    }

    assert len(request_hashes) == len(requests)
    assert any(repetition == "2" for _case_id, repetition in repetition_keys)


def test_cost_safe_execution_design_creates_primary_and_stability_work_items() -> None:
    benchmark_dir = PROJECT_ROOT / "data/benchmark/f7_release_scale"
    cases = load_benchmark_cases(benchmark_dir)
    manifest = build_openai_cost_safe_manifest(
        run_id="unit-cost-safe-work",
        benchmark_dir=benchmark_dir,
        price_table_path=PRICE_TABLE,
        budget_usd=Decimal("14.50"),
    )

    work_items = create_work_items(manifest, cases)
    primary = [item for item in work_items if item.analysis_role is AnalysisRole.PRIMARY]
    stability = [item for item in work_items if item.analysis_role is AnalysisRole.STABILITY]

    assert len(work_items) == 6000
    assert len(primary) == 4800
    assert len(stability) == 1200
    assert {item.repetition for item in primary} == {1}
    assert {item.repetition for item in stability} == {2, 3}
    assert len({item.work_item_id for item in work_items}) == 6000


def test_batch_and_paid_modes_fail_closed_before_dispatch(tmp_path: Path) -> None:
    batch = _adversarial_a3_manifest("unit-batch").model_copy(update={"mode": ExperimentMode.BATCH})
    paid = _adversarial_a3_manifest("unit-paid").model_copy(
        update={"allow_paid": True, "budget_usd": Decimal("1")}
    )
    batch_path = tmp_path / "batch.json"
    paid_path = tmp_path / "paid.json"
    write_run_manifest(batch, batch_path, overwrite=True)
    write_run_manifest(paid, paid_path, overwrite=True)

    with pytest.raises(ProjectError, match="Batch execution"):
        run_experiment(
            manifest_path=batch_path,
            benchmark_dir=BENCHMARK_DIR,
            output_dir=tmp_path / "batch-run",
            resume=True,
        )
    with pytest.raises(ProjectError, match="price table path"):
        run_experiment(
            manifest_path=paid_path,
            benchmark_dir=BENCHMARK_DIR,
            output_dir=tmp_path / "paid-run",
            allow_paid=True,
            resume=True,
        )


def test_paid_openai_run_uses_price_hash_cache_and_budget_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    price_path = tmp_path / "prices.json"
    price_path.write_text(_fresh_price_table().model_dump_json(), encoding="utf-8")
    base_manifest = build_fake_pilot_manifest(
        run_id="unit-paid-openai",
        benchmark_dir=BENCHMARK_DIR,
        price_table_path=price_path,
    )
    manifest = base_manifest.model_copy(
        update={
            "allow_paid": True,
            "approaches": [Approach.A3],
            "budget_usd": Decimal("0.10"),
            "model": ExperimentModelConfig(
                provider="openai",
                id="gpt-5.6-luna",
                reasoning_effort="low",
                parameters={"temperature": 0, "max_output_tokens": 77},
            ),
            "prompts": {Approach.A3: base_manifest.prompts[Approach.A3]},
            "splits": [DatasetSplit.ADVERSARIAL],
        }
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)
    payloads = fixture_payloads_from_benchmark(BENCHMARK_DIR)
    calls = 0

    def fake_complete(self, request):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return ProviderResponse(
            schema_version="1.0",
            provider="openai",
            model=request.model,
            response_id=f"resp-{calls}",
            finish_status=ProviderFinishStatus.STOP,
            raw_payload={"id": f"resp-{calls}", "model": request.model},
            parsed_payload=payloads[request.metadata["case_id"]],
            usage=ProviderUsage(input_tokens=10, output_tokens=5),
            cost=CostEstimate(estimated_usd=Decimal("0.000008")),
            timing_ms=1,
            attempts=1,
        )

    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key-not-printed")
    monkeypatch.setattr("semplan.experiments.runner.OpenAIProvider.complete", fake_complete)

    first = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        allow_paid=True,
        resume=True,
        max_items=1,
        price_table_path=price_path,
        cache_dir=tmp_path / "cache",
    )
    second = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        allow_paid=True,
        resume=True,
        price_table_path=price_path,
        cache_dir=tmp_path / "cache",
    )

    assert first["status"] == "interrupted"
    assert second["status"] == "completed"
    assert calls == 2
    assert Decimal(second["actual_api_spend_usd"]) > Decimal("0")
    assert (run_dir / "budget_ledger.json").is_file()
    assert validate_run_dir(run_dir)["record_count"] == 2


def test_runner_records_provider_failure_without_erasing_raw_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _adversarial_a3_manifest("unit-provider-failure")
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    def fail_complete(self: FakeProvider, request):  # type: ignore[no-untyped-def]
        raise ProjectError(ErrorCode.PROVIDER_REFUSAL, "synthetic refusal")

    monkeypatch.setattr(FakeProvider, "complete", fail_complete)

    summary = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=1,
    )

    assert summary["status"] == "interrupted"
    assert summary["result_record_count"] == 1
    assert "failed_terminal" in (run_dir / "work_ledger.json").read_text(encoding="utf-8")


def test_recovery_resets_terminal_without_provider_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _adversarial_a3_manifest("unit-terminal-recovery").model_copy(
        update={"splits": [DatasetSplit.DEVELOPMENT]}
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    cache_dir = tmp_path / "cache"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    def fail_complete(self: FakeProvider, request):  # type: ignore[no-untyped-def]
        raise ProjectError(ErrorCode.OUTPUT_SCHEMA_INVALID, "synthetic parse failure")

    monkeypatch.setattr(FakeProvider, "complete", fail_complete)

    run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=1,
    )

    report = reconcile_partial_run_state(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        run_dir=run_dir,
        cache_dir=cache_dir,
        output_path=run_dir / "reconciliation.json",
    )
    ledger = json.loads((run_dir / "work_ledger.json").read_text(encoding="utf-8"))

    assert report["affected"][0]["action"] == "reset_terminal_without_provider_evidence"
    assert any(item["status"] == "pending" for item in ledger["work_items"].values())
    assert (Path(report["backup_dir"]) / "recovered_terminal_records").is_dir()


def test_recovery_archives_pending_in_flight_cache_entry(tmp_path: Path) -> None:
    manifest = _adversarial_a3_manifest("unit-inflight-recovery").model_copy(
        update={"splits": [DatasetSplit.DEVELOPMENT]}
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    cache_dir = tmp_path / "cache"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=0,
    )
    planned = _planned_requests(manifest, BENCHMARK_DIR)
    request = next(iter(planned.values()))
    reservation = ProviderCache(cache_dir).reserve(request)
    ProviderCache(cache_dir).mark_in_flight(reservation)

    report = reconcile_partial_run_state(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        run_dir=run_dir,
        cache_dir=cache_dir,
        output_path=run_dir / "reconciliation.json",
    )

    assert report["affected"][0]["action"] == "reset_and_archived_stale_in_flight_cache_entry"
    assert report["after"]["cache_counts"] == {}
    assert (Path(report["backup_dir"]) / "cache_in_flight").is_dir()


def test_runner_records_provider_backed_execution_failure_as_scientific_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _adversarial_a3_manifest("unit-execution-failure").model_copy(
        update={"splits": [DatasetSplit.DEVELOPMENT]}
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    def fail_execute(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise ProjectError(ErrorCode.EXECUTION_FAILED, "synthetic DBAPI failure")

    monkeypatch.setattr(
        "semplan.approaches.semantic_plan.runner.execute_semantic_plan", fail_execute
    )

    summary = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=1,
    )

    records = [
        json.loads(line)
        for line in (run_dir / "records/result_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    ledger = json.loads((run_dir / "work_ledger.json").read_text(encoding="utf-8"))
    completed = [item for item in ledger["work_items"].values() if item["status"] == "completed"]

    assert summary["status"] == "interrupted"
    assert len(records) == 1
    assert records[0]["provider"] is not None
    assert records[0]["errors"][0]["code"] == ErrorCode.EXECUTION_FAILED.value
    assert records[0]["scores"]["unsafe_or_invalid"] is True
    assert len(completed) == 1
    assert "failed_terminal" not in (run_dir / "work_ledger.json").read_text(encoding="utf-8")


def test_runner_records_provider_backed_schema_failure_as_scientific_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _adversarial_a3_manifest("unit-schema-failure").model_copy(
        update={"splits": [DatasetSplit.DEVELOPMENT]}
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    def invalid_clarification(self: FakeProvider, request):  # type: ignore[no-untyped-def]
        return ProviderResponse(
            schema_version="1.0",
            provider="fake",
            model=request.model,
            response_id="fake-invalid-clarification",
            finish_status=ProviderFinishStatus.STOP,
            raw_payload={"id": "fake-invalid-clarification"},
            parsed_payload={
                "schema_version": "1.0",
                "operation": "CLARIFY",
                "intent": "ambiguity",
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "time_grain": None,
                "sort": [],
                "limit": None,
                "comparison": None,
                "clarifications": [
                    {
                        "reason_code": "AMBIGUOUS_FIELD",
                        "question": "Which date should be used?",
                        "options": ["Use the date/month field or the contract start date"],
                    }
                ],
                "out_of_scope_reason": None,
                "confidence": "0.6",
            },
            usage=ProviderUsage(input_tokens=10, output_tokens=8),
            cost=CostEstimate(estimated_usd=Decimal("0")),
            timing_ms=1,
            attempts=1,
        )

    monkeypatch.setattr(FakeProvider, "complete", invalid_clarification)

    summary = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=1,
    )

    record = json.loads(
        (run_dir / "records/result_records.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    ledger = json.loads((run_dir / "work_ledger.json").read_text(encoding="utf-8"))

    assert summary["status"] == "interrupted"
    assert record["provider"] is not None
    assert record["errors"][0]["code"] == ErrorCode.OUTPUT_SCHEMA_INVALID.value
    assert record["scores"]["unsafe_or_invalid"] is True
    assert any(item["status"] == "completed" for item in ledger["work_items"].values())


def test_runner_records_provider_error_response_with_raw_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _adversarial_a3_manifest("unit-provider-error-response").model_copy(
        update={"splits": [DatasetSplit.DEVELOPMENT]}
    )
    manifest_path = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    write_run_manifest(manifest, manifest_path, overwrite=True)

    def malformed_output(self: FakeProvider, request):  # type: ignore[no-untyped-def]
        return ProviderResponse(
            schema_version="1.0",
            provider="fake",
            model=request.model,
            response_id="fake-invalid-json",
            finish_status=ProviderFinishStatus.ERROR,
            raw_payload={
                "id": "fake-invalid-json",
                "output_text": "{not-json",
                "_semplan_parse_error": {"code": ErrorCode.OUTPUT_SCHEMA_INVALID.value},
            },
            parsed_payload=None,
            usage=ProviderUsage(input_tokens=10, output_tokens=8),
            cost=CostEstimate(estimated_usd=Decimal("0")),
            timing_ms=1,
            attempts=1,
        )

    monkeypatch.setattr(FakeProvider, "complete", malformed_output)

    summary = run_experiment(
        manifest_path=manifest_path,
        benchmark_dir=BENCHMARK_DIR,
        output_dir=run_dir,
        resume=True,
        max_items=1,
    )

    record = json.loads(
        (run_dir / "records/result_records.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    ledger = json.loads((run_dir / "work_ledger.json").read_text(encoding="utf-8"))

    assert summary["status"] == "interrupted"
    assert record["provider"] is not None
    assert record["provider"]["response_ref"]["path"].startswith("raw/")
    assert record["errors"][0]["code"] == ErrorCode.OUTPUT_SCHEMA_INVALID.value
    assert record["scores"]["unsafe_or_invalid"] is True
    assert any(item["status"] == "completed" for item in ledger["work_items"].values())


def _adversarial_a3_manifest(run_id: str):
    base_manifest = build_fake_pilot_manifest(
        run_id=run_id,
        benchmark_dir=BENCHMARK_DIR,
        price_table_path=PRICE_TABLE,
    )
    return base_manifest.model_copy(
        update={
            "approaches": [Approach.A3],
            "prompts": {Approach.A3: base_manifest.prompts[Approach.A3]},
            "splits": [DatasetSplit.ADVERSARIAL],
        }
    )


def _fresh_price_table() -> PriceTable:
    return PriceTable(
        schema_version="1.0",
        provider="openai",
        source="unit-test",
        checked_at_utc=datetime.now(UTC),
        currency="USD",
        model_prices={
            "gpt-5.6-luna": ModelPricing(
                input_per_million_usd=Decimal("0.20"),
                output_per_million_usd=Decimal("1.20"),
                cached_input_per_million_usd=Decimal("0.02"),
                batch_input_per_million_usd=Decimal("0.10"),
                batch_output_per_million_usd=Decimal("0.60"),
            )
        },
    )
