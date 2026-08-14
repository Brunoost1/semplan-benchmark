"""Offline request planning and cost estimation for frozen run manifests."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, NoReturn

from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.contracts import (
    CostEstimate,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderRequest,
)
from semplan.costs.pricing import estimate_request_tokens, load_price_table
from semplan.data_generation.writer import canonical_json
from semplan.experiments.design import validate_stability_execution_design
from semplan.experiments.manifest import (
    load_run_manifest,
    manifest_file_hash,
    validate_manifest_for_execution,
)
from semplan.experiments.runner import _runner_for_item, create_work_items
from semplan.prompts import PromptRegistry


class CapturedRequest(Exception):
    """Raised after a runner renders a provider request for offline planning."""


class CaptureOnlyProvider:
    def __init__(self, sink: list[ProviderRequest]) -> None:
        self.sink = sink

    def complete(self, request: ProviderRequest) -> NoReturn:
        self.sink.append(request)
        raise CapturedRequest

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        _ = request
        return CostEstimate(estimated_usd=Decimal("0"))

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.OK, provider="capture-only")


def estimate_manifest_cost(
    *,
    manifest_path: Path,
    benchmark_dir: Path,
    price_table_path: Path,
    smoke_report_path: Path,
    previous_estimate_path: Path,
    output_path: Path | None = None,
    project_budget_usd: Decimal = Decimal("20.00"),
    reserved_safety_margin_usd: Decimal = Decimal("5.00"),
    known_successful_smoke_spend_usd: Decimal = Decimal("0.000627"),
    unknown_prior_provider_response_reservation_usd: Decimal = Decimal("0.010000"),
    runtime_hard_stop_usd: Decimal = Decimal("14.50"),
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Render every planned request locally and estimate Standard API costs."""

    manifest = load_run_manifest(manifest_path)
    preflight = validate_manifest_for_execution(
        manifest,
        benchmark_dir=benchmark_dir,
        allow_paid=True,
    )
    price_table = load_price_table(price_table_path)
    pricing = price_table.model_prices[manifest.model.id]
    previous_estimate = json.loads(previous_estimate_path.read_text(encoding="utf-8"))
    smoke_report = json.loads(smoke_report_path.read_text(encoding="utf-8"))
    smoke_usage = smoke_report["provider_response"]["usage"]

    cases = load_benchmark_cases(benchmark_dir)
    case_map = {case.case_id: case for case in cases}
    work_items = create_work_items(
        manifest,
        [case for case in cases if case.split in manifest.splits],
    )
    requests: list[ProviderRequest] = []
    catalog = load_catalog(Path("catalog"))
    prompts = PromptRegistry.load(Path("prompts"))

    by_approach: dict[str, dict[str, int]] = defaultdict(
        lambda: {"requests": 0, "raw_input_tokens": 0, "max_output_tokens": 0}
    )
    by_analysis_role: Counter[str] = Counter()
    request_language_counts: Counter[str] = Counter()
    request_split_counts: Counter[str] = Counter()

    for item in work_items:
        case = case_map[item.case_id]
        before = len(requests)
        runner = _runner_for_item(
            approach=item.approach,
            provider=CaptureOnlyProvider(requests),
            catalog=catalog,
            prompts=prompts,
            manifest=manifest,
            item=item,
            database_url=None,
        )
        try:
            runner.run_case(case)
        except CapturedRequest:
            pass
        if len(requests) != before + 1:
            raise RuntimeError(f"failed to capture one request for {item.work_item_id}")
        input_tokens, output_tokens = estimate_request_tokens(requests[-1])
        bucket = by_approach[item.approach.value]
        bucket["requests"] += 1
        bucket["raw_input_tokens"] += input_tokens
        bucket["max_output_tokens"] += output_tokens
        by_analysis_role[item.analysis_role.value] += 1
        request_language_counts[case.language.value] += 1
        request_split_counts[case.split.value] += 1

    raw_input_tokens = sum(estimate_request_tokens(request)[0] for request in requests)
    max_output_tokens = sum(estimate_request_tokens(request)[1] for request in requests)
    calibration = Decimal(str(previous_estimate["input_estimator_calibration_factor"]))
    calibrated_input_tokens = int(
        (Decimal(raw_input_tokens) * calibration).to_integral_value(rounding=ROUND_HALF_UP)
    )
    observed_output_per_request = int(smoke_usage["output_tokens"]) + int(
        smoke_usage.get("reasoning_tokens", 0)
    )
    observed_output_tokens = observed_output_per_request * len(requests)
    request_hashes = [request.idempotency_hash for request in requests]
    duplicate_request_hash_count = len(request_hashes) - len(set(request_hashes))
    selected_case_ids = (
        manifest.execution_design.scientific_case_ids
        if manifest.execution_design is not None
        else sorted(case.case_id for case in cases if case.split in manifest.splits)
    )
    selected_cases = [case_map[case_id] for case_id in selected_case_ids]
    stability_validation = (
        validate_stability_execution_design(
            benchmark_dir=benchmark_dir,
            manifest_execution_design=manifest.execution_design,
        )
        if manifest.execution_design is not None
        else None
    )

    expected_raw = _standard_cost(
        input_tokens=calibrated_input_tokens,
        output_tokens=observed_output_tokens,
        cached_input_tokens=0,
        input_price=pricing.input_per_million_usd,
        cached_input_price=pricing.cached_input_per_million_usd,
        output_price=pricing.output_per_million_usd,
    )
    conservative_raw = _standard_cost(
        input_tokens=calibrated_input_tokens,
        output_tokens=max_output_tokens,
        cached_input_tokens=0,
        input_price=pricing.input_per_million_usd,
        cached_input_price=pricing.cached_input_per_million_usd,
        output_price=pricing.output_per_million_usd,
    )
    safety = Decimal("1.20")
    remaining_project_budget = (
        project_budget_usd
        - known_successful_smoke_spend_usd
        - unknown_prior_provider_response_reservation_usd
    )
    available_execution_ceiling = remaining_project_budget - reserved_safety_margin_usd
    conservative_with_safety = conservative_raw * safety
    expected_with_safety = expected_raw * safety
    ready = (
        conservative_with_safety < runtime_hard_stop_usd
        and duplicate_request_hash_count == 0
        and (stability_validation is None or bool(stability_validation["ok"]))
    )

    result = {
        "schema_version": "1.0",
        "created_at_utc": created_at_utc or datetime.now().astimezone().isoformat(),
        "basis": "Exact offline render of frozen RunManifest work items; no provider calls.",
        "manifest": {
            "path": str(manifest_path),
            "sha256": manifest_file_hash(manifest_path),
            "run_id": manifest.run_id,
            "code_commit": manifest.code_commit,
            "dirty_tree": manifest.dirty_tree,
            "budget_usd": str(manifest.budget_usd),
            "runtime_hard_stop_usd": str(runtime_hard_stop_usd),
            "execution_design": manifest.execution_design.model_dump(mode="json")
            if manifest.execution_design is not None
            else None,
        },
        "benchmark": {
            "path": str(benchmark_dir),
            "benchmark_version": manifest.benchmark_version,
            "manifest_sha256": manifest_file_hash(benchmark_dir / "benchmark_manifest.json"),
            "dataset_manifest_sha256": manifest.dataset_manifest_sha256,
            "all_case_count": len(cases),
            "selected_case_count": len(selected_cases),
        },
        "model": manifest.model.model_dump(mode="json"),
        "price_table": {
            "path": str(price_table_path),
            "sha256": manifest_file_hash(price_table_path),
            "checked_at_utc": price_table.checked_at_utc.isoformat(),
            "source": price_table.source,
            "standard_input_per_million_usd": str(pricing.input_per_million_usd),
            "standard_cached_input_per_million_usd": str(pricing.cached_input_per_million_usd),
            "standard_output_per_million_usd": str(pricing.output_per_million_usd),
        },
        "workload": {
            "scientific_case_count": len(selected_cases),
            "approaches": [approach.value for approach in manifest.approaches],
            "primary_executions": by_analysis_role["primary"],
            "stability_additional_executions": by_analysis_role["stability"],
            "request_count": len(requests),
            "unique_request_hash_count": len(set(request_hashes)),
            "duplicate_request_hash_count": duplicate_request_hash_count,
            "raw_input_tokens": raw_input_tokens,
            "expected_input_tokens": calibrated_input_tokens,
            "conservative_input_tokens": calibrated_input_tokens,
            "expected_output_tokens": observed_output_tokens,
            "conservative_output_tokens": max_output_tokens,
            "expected_cached_input_tokens_first_run": 0,
            "observed_output_tokens_per_request_from_smoke": observed_output_per_request,
            "by_approach": dict(sorted(by_approach.items())),
            "request_language_counts": dict(sorted(request_language_counts.items())),
            "request_split_counts": dict(sorted(request_split_counts.items())),
        },
        "costs": {
            "expected_standard_raw_usd": _money(expected_raw),
            "expected_standard_with_1_20_safety_usd": _money(expected_with_safety),
            "conservative_standard_raw_usd": _money(conservative_raw),
            "conservative_standard_with_1_20_safety_usd": _money(conservative_with_safety),
            "known_successful_smoke_spend_usd": str(known_successful_smoke_spend_usd),
            "unknown_prior_provider_response_reservation_usd": str(
                unknown_prior_provider_response_reservation_usd
            ),
            "project_budget_usd": str(project_budget_usd),
            "remaining_project_budget_usd": _money(remaining_project_budget),
            "reserved_safety_margin_usd": str(reserved_safety_margin_usd),
            "available_execution_ceiling_usd": _money(available_execution_ceiling),
            "runtime_hard_stop_usd": str(runtime_hard_stop_usd),
            "budget_headroom_under_hard_stop_usd": _money(
                runtime_hard_stop_usd - conservative_with_safety
            ),
        },
        "idempotency": {
            "planned_duplicate_request_hashes": duplicate_request_hash_count,
            "first_run_expected_cache_hits": 0,
            "cache_namespace_requirement": "Use a fresh scientific cache directory.",
            "request_identity": (
                "run_id, case_id, approach, repetition, analysis_role, and work_item_id "
                "are included for cost-safe manifests."
            ),
        },
        "stability_validation": stability_validation,
        "preflight": preflight,
        "readiness": {
            "final_paid_execution_ready": ready,
            "blocking_items": []
            if ready
            else _blocking_items(
                conservative_with_safety=conservative_with_safety,
                runtime_hard_stop_usd=runtime_hard_stop_usd,
                duplicate_request_hash_count=duplicate_request_hash_count,
                stability_validation=stability_validation,
            ),
        },
    }
    result["sha256"] = _sha_obj({key: value for key, value in result.items() if key != "sha256"})
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(canonical_json(result) + "\n", encoding="utf-8", newline="\n")
    return result


def _standard_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    input_price: Decimal,
    cached_input_price: Decimal,
    output_price: Decimal,
) -> Decimal:
    uncached_input_tokens = input_tokens - cached_input_tokens
    return (
        Decimal(uncached_input_tokens) * input_price
        + Decimal(cached_input_tokens) * cached_input_price
        + Decimal(output_tokens) * output_price
    ) / Decimal("1000000")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _sha_obj(value: object) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _blocking_items(
    *,
    conservative_with_safety: Decimal,
    runtime_hard_stop_usd: Decimal,
    duplicate_request_hash_count: int,
    stability_validation: dict[str, Any] | None,
) -> list[str]:
    items: list[str] = []
    if conservative_with_safety >= runtime_hard_stop_usd:
        items.append("Conservative Standard projection is not below the runtime hard stop.")
    if duplicate_request_hash_count:
        items.append("Planned provider request hashes are not unique.")
    if stability_validation is not None and not stability_validation["ok"]:
        items.append("Stability subset validation failed.")
    return items
