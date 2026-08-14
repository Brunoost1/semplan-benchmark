"""Deterministic experiment runner, work ledger, and fake/replay pilot execution."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.approaches.direct_sql import DirectSqlRunner, DirectSqlRunResult
from semplan.approaches.semantic_plan import (
    SemanticPlanRunner,
    SemanticPlanRunResult,
    default_prompt_registry,
    direct_sql_payloads_from_benchmark,
    fixture_payloads_from_benchmark,
    tool_agent_payloads_from_benchmark,
)
from semplan.approaches.tool_agent import ToolAgentRunner, ToolAgentRunResult
from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.catalog.models import Catalog
from semplan.contracts import (
    AnalysisRole,
    Approach,
    ArtifactRef,
    BenchmarkCase,
    CostEstimate,
    ExpectedPolicy,
    ExperimentMode,
    GoldAnswer,
    PredictionStatus,
    PriceTable,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderRequest,
    ProviderResponse,
    ResultError,
    ResultExecutionRef,
    ResultInputRef,
    ResultOutcome,
    ResultPredictionRef,
    ResultProviderRef,
    ResultRecord,
    RunManifest,
    ScalarValue,
    SemanticPlanEnvelope,
    SemanticRequestEnvelope,
    WorkItemStatus,
)
from semplan.costs import BudgetController, BudgetLedger, load_price_table
from semplan.costs.pricing import ensure_price_table_fresh
from semplan.data_generation.writer import canonical_json
from semplan.errors import ErrorCode, ProjectError
from semplan.evaluation import canonicalize_row
from semplan.experiments.artifacts import (
    rebuild_result_jsonl,
    validate_experiment_directory,
    write_json_artifact,
    write_result_record,
)
from semplan.experiments.manifest import (
    copy_manifest_for_run,
    load_run_manifest,
    manifest_file_hash,
    validate_manifest_for_execution,
)
from semplan.experiments.reporting import generate_analysis_artifacts
from semplan.experiments.scoring import score_case
from semplan.prompts import PromptRegistry
from semplan.providers import (
    BudgetedProvider,
    CachedProvider,
    FakeProvider,
    ModelProvider,
    OpenAIProvider,
    ProviderCache,
)
from semplan.runtime_env import openai_api_key

RunnerResult = DirectSqlRunResult | ToolAgentRunResult | SemanticPlanRunResult


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    case_id: str
    approach: Approach
    repetition: int
    ordinal: int
    analysis_role: AnalysisRole = AnalysisRole.PRIMARY


class CapturingProvider:
    """Provider wrapper that records requests and responses for artifact persistence."""

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner
        self.turns: list[tuple[ProviderRequest, ProviderResponse]] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        response = self.inner.complete(request)
        self.turns.append((request, response))
        return response

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        return self.inner.estimate_cost(request)

    def healthcheck(self) -> ProviderHealth:
        return self.inner.healthcheck()


def create_work_items(manifest: RunManifest, cases: list[BenchmarkCase]) -> list[WorkItem]:
    if manifest.execution_design is not None:
        case_map = {case.case_id: case for case in cases}
        selected = [case_map[case_id] for case_id in manifest.execution_design.scientific_case_ids]
        stability_ids = set(manifest.execution_design.stability_subset_case_ids)
        primary_repetitions = manifest.execution_design.primary_repetitions
    else:
        selected = [case for case in cases if case.split in manifest.splits]
        stability_ids = set()
        primary_repetitions = manifest.repetitions
    rng = random.Random(manifest.randomization_seed)
    shuffled = list(selected)
    rng.shuffle(shuffled)
    work_items: list[WorkItem] = []
    ordinal = 0
    for repetition in range(1, manifest.repetitions + 1):
        repetition_cases = (
            shuffled
            if repetition <= primary_repetitions
            else [case for case in shuffled if case.case_id in stability_ids]
        )
        analysis_role = (
            AnalysisRole.PRIMARY if repetition <= primary_repetitions else AnalysisRole.STABILITY
        )
        for case in repetition_cases:
            for approach in manifest.approaches:
                work_items.append(
                    WorkItem(
                        work_item_id=_work_item_id(
                            manifest.run_id,
                            case.case_id,
                            approach,
                            repetition,
                            analysis_role if manifest.execution_design is not None else None,
                        ),
                        case_id=case.case_id,
                        approach=approach,
                        repetition=repetition,
                        ordinal=ordinal,
                        analysis_role=analysis_role,
                    )
                )
                ordinal += 1
    return work_items


def run_experiment(
    *,
    manifest_path: Path,
    benchmark_dir: Path,
    output_dir: Path,
    allow_paid: bool = False,
    resume: bool = False,
    max_items: int | None = None,
    database_url: str | None = None,
    price_table_path: Path | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    manifest = load_run_manifest(manifest_path)
    if manifest.mode is ExperimentMode.BATCH:
        raise ProjectError(ErrorCode.CFG_INVALID, "Batch execution is not implemented in F6")
    preflight = validate_manifest_for_execution(
        manifest,
        benchmark_dir=benchmark_dir,
        allow_paid=allow_paid,
    )
    price_table = None
    if manifest.allow_paid or manifest.model.provider == "openai":
        if price_table_path is None:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Paid or OpenAI experiment execution requires a price table path",
            )
        actual_price_hash = manifest_file_hash(price_table_path)
        if actual_price_hash != manifest.price_table_sha256:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Price table hash does not match run manifest",
                detail={
                    "expected": manifest.price_table_sha256,
                    "actual": actual_price_hash,
                },
            )
        price_table = load_price_table(price_table_path)
        ensure_price_table_fresh(price_table)
    copy_manifest_for_run(manifest_path, output_dir)
    cases = load_benchmark_cases(benchmark_dir)
    case_map = {case.case_id: case for case in cases if case.split in manifest.splits}
    work_items = create_work_items(manifest, list(case_map.values()))
    _write_exclusions(output_dir, cases, manifest)

    ledger = _load_or_create_ledger(output_dir, manifest, work_items, resume=resume)
    if manifest.mode is ExperimentMode.DRY_RUN:
        _write_run_summary(
            output_dir,
            manifest=manifest,
            status="dry_run",
            preflight=preflight,
            executed_count=0,
            work_item_count=len(work_items),
        )
        summary = json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ProjectError(ErrorCode.CFG_INVALID, "Run summary must be a JSON object")
        return summary

    provider_factory = _provider_factory(
        benchmark_dir=benchmark_dir,
        output_dir=output_dir,
        manifest=manifest,
        allow_paid=allow_paid,
        price_table=price_table,
        cache_dir=cache_dir,
    )
    catalog = load_catalog(Path("catalog"))
    prompts = default_prompt_registry()
    executed_count = 0
    interrupted = False
    for item in work_items:
        ledger_item = ledger["work_items"][item.work_item_id]
        if _is_completed(output_dir, ledger_item):
            continue
        if max_items is not None and executed_count >= max_items:
            interrupted = True
            break
        _mark_ledger(
            output_dir,
            ledger,
            item,
            WorkItemStatus.RUNNING,
            manifest.created_at_utc + timedelta(seconds=item.ordinal * 2),
        )
        provider = CapturingProvider(provider_factory(item.approach))
        case = case_map[item.case_id]
        try:
            runner = _runner_for_item(
                approach=item.approach,
                provider=provider,
                catalog=catalog,
                prompts=prompts,
                manifest=manifest,
                item=item,
                database_url=database_url,
            )
            result = runner.run_case(case)
            record = _record_success(
                output_dir=output_dir,
                manifest=manifest,
                item=item,
                case=case,
                result=result,
                provider=provider,
                benchmark_dir=benchmark_dir,
            )
            _mark_ledger(
                output_dir,
                ledger,
                item,
                WorkItemStatus.COMPLETED,
                manifest.created_at_utc + timedelta(seconds=item.ordinal * 2 + 1),
                result_record_ref=f"records/{record.work_item_id.removeprefix('sha256:')}.json",
            )
        except ProjectError as exc:
            record = _record_failure(
                output_dir=output_dir,
                manifest=manifest,
                item=item,
                case=case,
                provider=provider,
                error=ResultError(
                    code=exc.record.code.value,
                    message=exc.record.message,
                    retryable=exc.record.retryable,
                    layer=exc.record.layer,
                ),
                benchmark_dir=benchmark_dir,
            )
            _mark_ledger(
                output_dir,
                ledger,
                item,
                _failure_status(record),
                manifest.created_at_utc + timedelta(seconds=item.ordinal * 2 + 1),
                result_record_ref=f"records/{record.work_item_id.removeprefix('sha256:')}.json",
            )
        executed_count += 1

    rebuild_result_jsonl(output_dir)
    final_status = "interrupted" if interrupted else _terminal_status(ledger)
    summary = _write_run_summary(
        output_dir,
        manifest=manifest,
        status=final_status,
        preflight=preflight,
        executed_count=executed_count,
        work_item_count=len(work_items),
    )
    if final_status == "completed":
        validate_experiment_directory(output_dir)
        generate_analysis_artifacts(run_dir=output_dir, benchmark_dir=benchmark_dir)
    return summary


def validate_run_dir(run_dir: Path) -> dict[str, Any]:
    return validate_experiment_directory(run_dir)


def regenerate_paper_artifacts(*, run_dir: Path, benchmark_dir: Path) -> dict[str, Any]:
    return generate_analysis_artifacts(run_dir=run_dir, benchmark_dir=benchmark_dir)


def _runner_for_item(
    *,
    approach: Approach,
    provider: ModelProvider,
    catalog: Catalog,
    prompts: PromptRegistry,
    manifest: RunManifest,
    item: WorkItem,
    database_url: str | None,
) -> Any:
    if approach is Approach.A1:
        return DirectSqlRunner(
            provider=provider,
            catalog=catalog,
            prompt_registry=prompts,
            database_url=database_url,
            provider_name=manifest.model.provider,
            model_name=manifest.model.id,
            inference_parameters=_manifest_inference_parameters(manifest),
            request_metadata=_work_item_request_metadata(manifest, item),
        )
    if approach is Approach.A2:
        return ToolAgentRunner(
            provider=provider,
            catalog=catalog,
            prompt_registry=prompts,
            database_url=database_url,
            provider_name=manifest.model.provider,
            model_name=manifest.model.id,
            inference_parameters=_manifest_inference_parameters(manifest),
            request_metadata=_work_item_request_metadata(manifest, item),
        )
    return SemanticPlanRunner(
        approach=approach,
        provider=provider,
        catalog=catalog,
        prompt_registry=prompts,
        database_url=database_url,
        provider_name=manifest.model.provider,
        model_name=manifest.model.id,
        inference_parameters=_manifest_inference_parameters(manifest),
        request_metadata=_work_item_request_metadata(manifest, item),
    )


def _manifest_inference_parameters(manifest: RunManifest) -> dict[str, object]:
    parameters: dict[str, object] = dict(manifest.model.parameters)
    if manifest.model.provider == "openai":
        parameters.setdefault("reasoning_effort", manifest.model.reasoning_effort)
    return parameters


def _work_item_request_metadata(manifest: RunManifest, item: WorkItem) -> dict[str, str]:
    metadata = {
        "run_id": manifest.run_id,
        "repetition": str(item.repetition),
        "work_item_id": item.work_item_id,
    }
    if manifest.execution_design is not None:
        metadata["analysis_role"] = item.analysis_role.value
        metadata["execution_design_id"] = manifest.execution_design.design_id
    return metadata


def _record_success(
    *,
    output_dir: Path,
    manifest: RunManifest,
    item: WorkItem,
    case: BenchmarkCase,
    result: RunnerResult,
    provider: CapturingProvider,
    benchmark_dir: Path,
) -> ResultRecord:
    if not provider.turns:
        raise ProjectError(ErrorCode.CFG_INVALID, "Runner did not capture provider turn")
    request, response = provider.turns[-1]
    provider_ref = _provider_ref(output_dir, item, request, response)
    prediction_ref = _prediction_ref(output_dir, item, result)
    execution_ref = _execution_ref(output_dir, item, case.expected_policy, result)
    gold_answer = _gold_answer(benchmark_dir, case)
    gold_plan = _gold_plan(benchmark_dir, case)
    semantic_request = _semantic_request(result)
    rows = _result_rows(result)
    executed_database = execution_ref.executed_database
    scores = score_case(
        case=case,
        gold_answer=gold_answer,
        outcome=result.outcome,
        rows=rows,
        executed_database=executed_database,
        semantic_request=semantic_request,
        gold_plan=gold_plan,
        cost_usd=response.cost.estimated_usd,
        latency_ms=response.timing_ms,
        provider_latency_ms=response.timing_ms,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
    score_ref = write_json_artifact(
        output_dir,
        f"scores/{item.work_item_id.removeprefix('sha256:')}.json",
        scores,
    )
    record = ResultRecord(
        schema_version="1.0",
        run_id=manifest.run_id,
        work_item_id=item.work_item_id,
        case_id=case.case_id,
        approach=item.approach,
        repetition=item.repetition,
        analysis_role=item.analysis_role,
        input=_input_ref(case),
        provider=provider_ref,
        prediction=prediction_ref,
        execution=execution_ref,
        scores=scores,
        score_ref=score_ref,
        errors=[],
        timestamps=_timestamps(manifest, item),
        outcome=result.outcome,
    )
    write_result_record(output_dir, record)
    return record


def _record_failure(
    *,
    output_dir: Path,
    manifest: RunManifest,
    item: WorkItem,
    case: BenchmarkCase,
    provider: CapturingProvider,
    error: ResultError,
    benchmark_dir: Path,
) -> ResultRecord:
    provider_ref = None
    if provider.turns:
        request, response = provider.turns[-1]
        provider_ref = _provider_ref(output_dir, item, request, response)
    gold_answer = _gold_answer(benchmark_dir, case)
    scores = score_case(
        case=case,
        gold_answer=gold_answer,
        outcome=ResultOutcome.ERROR,
        rows=None,
        executed_database=False,
        semantic_request=None,
        gold_plan=None,
        cost_usd=provider_ref.cost_usd if provider_ref is not None else Decimal("0"),
        latency_ms=0,
        provider_latency_ms=0,
        input_tokens=provider_ref.usage.input_tokens if provider_ref is not None else 0,
        output_tokens=provider_ref.usage.output_tokens if provider_ref is not None else 0,
        error_count=1,
    )
    score_ref = write_json_artifact(
        output_dir,
        f"scores/{item.work_item_id.removeprefix('sha256:')}.json",
        scores,
    )
    record = ResultRecord(
        schema_version="1.0",
        run_id=manifest.run_id,
        work_item_id=item.work_item_id,
        case_id=case.case_id,
        approach=item.approach,
        repetition=item.repetition,
        analysis_role=item.analysis_role,
        input=_input_ref(case),
        provider=provider_ref,
        prediction=ResultPredictionRef(status=PredictionStatus.ERROR, artifact_ref=None),
        execution=ResultExecutionRef(
            policy=case.expected_policy,
            executed_database=False,
            query_sha256=None,
            duration_ms=0,
            row_count=0,
            result_ref=None,
        ),
        scores=scores,
        score_ref=score_ref,
        errors=[error],
        timestamps=_timestamps(manifest, item),
        outcome=ResultOutcome.ERROR,
    )
    write_result_record(output_dir, record)
    return record


def _provider_ref(
    output_dir: Path,
    item: WorkItem,
    request: ProviderRequest,
    response: ProviderResponse,
) -> ResultProviderRef:
    suffix = item.work_item_id.removeprefix("sha256:")
    request_ref = write_json_artifact(output_dir, f"rendered_prompts/{suffix}.json", request)
    response_ref = write_json_artifact(output_dir, f"raw/{suffix}.json", response)
    return ResultProviderRef(
        request_sha256=request.idempotency_hash,
        request_ref=request_ref,
        response_ref=response_ref,
        model_requested=request.model,
        model_returned=response.model,
        usage=response.usage,
        cost_usd=response.cost.estimated_usd,
    )


def _prediction_ref(output_dir: Path, item: WorkItem, result: RunnerResult) -> ResultPredictionRef:
    suffix = item.work_item_id.removeprefix("sha256:")
    payload: object
    if isinstance(result, DirectSqlRunResult):
        payload = result.direct_sql
    elif isinstance(result, ToolAgentRunResult):
        payload = result.turn
    else:
        payload = result.semantic_request
    artifact_ref = write_json_artifact(output_dir, f"predictions/{suffix}.json", payload)
    return ResultPredictionRef(status=PredictionStatus.PARSED, artifact_ref=artifact_ref)


def _execution_ref(
    output_dir: Path,
    item: WorkItem,
    policy: ExpectedPolicy,
    result: RunnerResult,
) -> ResultExecutionRef:
    suffix = item.work_item_id.removeprefix("sha256:")
    execution = result.execution
    response_ref = write_json_artifact(output_dir, f"canonical/{suffix}.json", result.response)
    query_sha256 = None
    row_count = 0
    if execution is not None:
        query_sha256 = execution.compiled_query.sql_sha256
        row_count = execution.row_count
        write_json_artifact(
            output_dir,
            f"compiled_queries/{suffix}.json",
            {
                "sql_sha256": execution.compiled_query.sql_sha256,
                "guard_sql": execution.compiled_query.guard_sql,
            },
        )
    return ResultExecutionRef(
        policy=policy,
        executed_database=execution is not None,
        query_sha256=query_sha256,
        duration_ms=0,
        row_count=row_count,
        result_ref=response_ref,
    )


def _semantic_request(result: RunnerResult) -> SemanticRequestEnvelope | None:
    if isinstance(result, ToolAgentRunResult):
        return result.turn.final_request
    if isinstance(result, SemanticPlanRunResult):
        return result.semantic_request
    return None


def _result_rows(result: RunnerResult) -> list[dict[str, ScalarValue]] | None:
    if result.execution is None:
        return None
    return [canonicalize_row(dict(row), {}) for row in result.execution.rows]


def _gold_answer(benchmark_dir: Path, case: BenchmarkCase) -> GoldAnswer:
    return GoldAnswer.model_validate_json(
        (benchmark_dir / case.gold_answer_ref).read_text(encoding="utf-8")
    )


def _gold_plan(benchmark_dir: Path, case: BenchmarkCase) -> SemanticPlanEnvelope | None:
    if case.gold_semantic_plan_ref is None:
        return None
    return SemanticPlanEnvelope.model_validate_json(
        (benchmark_dir / case.gold_semantic_plan_ref).read_text(encoding="utf-8")
    )


def _input_ref(case: BenchmarkCase) -> ResultInputRef:
    digest = hashlib.sha256(case.utterance.encode("utf-8")).hexdigest()
    return ResultInputRef(
        utterance_sha256=f"sha256:{digest}",
        state_sha256=None,
        split=case.split,
        language=case.language,
    )


def _timestamps(manifest: RunManifest, item: WorkItem) -> dict[str, datetime]:
    base = manifest.created_at_utc + timedelta(seconds=item.ordinal * 2)
    return {"started_at": base, "completed_at": base + timedelta(seconds=1)}


def _fake_provider_factory(
    benchmark_dir: Path,
    manifest: RunManifest,
) -> Callable[[Approach], FakeProvider]:
    payloads = {
        Approach.A1: direct_sql_payloads_from_benchmark(benchmark_dir),
        Approach.A2: tool_agent_payloads_from_benchmark(benchmark_dir),
        Approach.A3: fixture_payloads_from_benchmark(benchmark_dir),
        Approach.A4: fixture_payloads_from_benchmark(benchmark_dir),
    }

    def factory(approach: Approach) -> FakeProvider:
        return FakeProvider(
            payloads[approach],
            provider=manifest.model.provider,
            model=manifest.model.id,
        )

    return factory


def _provider_factory(
    *,
    benchmark_dir: Path,
    output_dir: Path,
    manifest: RunManifest,
    allow_paid: bool,
    price_table: PriceTable | None,
    cache_dir: Path | None,
) -> Callable[[Approach], ModelProvider]:
    if manifest.model.provider == "fake":
        return _fake_provider_factory(benchmark_dir, manifest)
    if manifest.model.provider != "openai":
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Unsupported experiment provider",
            detail={"provider": manifest.model.provider},
        )
    if manifest.mode is ExperimentMode.REPLAY:
        if cache_dir is None:
            raise ProjectError(ErrorCode.CFG_INVALID, "Replay mode requires --cache-dir")
        replay_provider = ExperimentCacheOnlyProvider(ProviderCache(cache_dir))
        return lambda _approach: replay_provider
    if not manifest.allow_paid or not allow_paid:
        raise ProjectError(ErrorCode.BUDGET_EXCEEDED, "OpenAI experiment requires --allow-paid")
    if price_table is None:
        raise ProjectError(ErrorCode.CFG_INVALID, "OpenAI experiment requires a price table")
    api_key = openai_api_key()
    if api_key is None:
        raise ProjectError(ErrorCode.CFG_INVALID, "OPENAI_API_KEY is required")
    resolved_cache_dir = cache_dir or output_dir / "provider_cache"
    budget_ledger = BudgetLedger(output_dir / "budget_ledger.json")
    budget = BudgetController(
        run_manifest=manifest,
        price_table=price_table,
        allow_paid=allow_paid,
        api_key_present=api_key is not None,
        ledger=budget_ledger,
        current_run_spend_usd=budget_ledger.total_spend_usd(),
    )
    openai_provider = OpenAIProvider(
        price_table=price_table,
        allow_paid=allow_paid,
        api_key=api_key,
    )
    cached_provider = CachedProvider(
        BudgetedProvider(openai_provider, budget),
        ProviderCache(resolved_cache_dir),
    )
    return lambda _approach: cached_provider


class ExperimentCacheOnlyProvider:
    """Provider implementation that refuses cache misses during replay mode."""

    def __init__(self, cache: ProviderCache) -> None:
        self.cache = cache

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        cached = self.cache.lookup(request)
        if cached is None:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Replay cache miss for provider request",
                detail={"request_hash": request.idempotency_hash},
            )
        return cached

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        _ = request
        return CostEstimate(estimated_usd=Decimal("0"))

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.OK, provider="openai-cache")


def _load_or_create_ledger(
    output_dir: Path,
    manifest: RunManifest,
    work_items: list[WorkItem],
    *,
    resume: bool,
) -> dict[str, Any]:
    path = output_dir / "work_ledger.json"
    if path.exists():
        if not resume:
            raise FileExistsError(f"Work ledger already exists: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ProjectError(ErrorCode.CFG_INVALID, "Work ledger must be an object")
        return raw
    ledger = {
        "schema_version": "1.0",
        "run_id": manifest.run_id,
        "work_items": {
            item.work_item_id: {
                "work_item_id": item.work_item_id,
                "case_id": item.case_id,
                "approach": item.approach.value,
                "repetition": item.repetition,
                "analysis_role": item.analysis_role.value,
                "ordinal": item.ordinal,
                "status": WorkItemStatus.PENDING.value,
                "result_record_ref": None,
                "updated_at": manifest.created_at_utc.isoformat(),
            }
            for item in work_items
        },
    }
    _write_ledger(path, ledger)
    return ledger


def _mark_ledger(
    output_dir: Path,
    ledger: dict[str, Any],
    item: WorkItem,
    status: WorkItemStatus,
    updated_at: datetime,
    *,
    result_record_ref: str | None = None,
) -> None:
    ledger_item = ledger["work_items"][item.work_item_id]
    ledger_item["status"] = status.value
    ledger_item["updated_at"] = updated_at.isoformat()
    if result_record_ref is not None:
        ledger_item["result_record_ref"] = result_record_ref
    _write_ledger(output_dir / "work_ledger.json", ledger)


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(canonical_json(ledger) + "\n", encoding="utf-8", newline="\n")
    tmp_path.replace(path)


def _is_completed(output_dir: Path, ledger_item: dict[str, Any]) -> bool:
    if ledger_item.get("status") != WorkItemStatus.COMPLETED.value:
        return False
    record_ref = ledger_item.get("result_record_ref")
    return isinstance(record_ref, str) and (output_dir / record_ref).exists()


def _failure_status(record: ResultRecord) -> WorkItemStatus:
    if record.provider is not None and not any(error.retryable for error in record.errors):
        return WorkItemStatus.COMPLETED
    if any(error.retryable for error in record.errors):
        return WorkItemStatus.FAILED_RETRYABLE
    return WorkItemStatus.FAILED_TERMINAL


def _terminal_status(ledger: dict[str, Any]) -> str:
    statuses = {item["status"] for item in ledger["work_items"].values()}
    if statuses == {WorkItemStatus.COMPLETED.value}:
        return "completed"
    if any(status == WorkItemStatus.FAILED_RETRYABLE.value for status in statuses):
        return "aborted_retryable"
    if any(status == WorkItemStatus.FAILED_TERMINAL.value for status in statuses):
        return "aborted"
    return "interrupted"


def _write_run_summary(
    output_dir: Path,
    *,
    manifest: RunManifest,
    status: str,
    preflight: dict[str, Any],
    executed_count: int,
    work_item_count: int,
) -> dict[str, Any]:
    records_path = output_dir / "records" / "result_records.jsonl"
    summary = {
        "schema_version": "1.0",
        "run_id": manifest.run_id,
        "status": status,
        "work_item_count": work_item_count,
        "result_record_count": _result_record_count(records_path),
        "executed_this_invocation": executed_count,
        "preflight": preflight,
        "estimated_api_spend_usd": "0",
        "actual_api_spend_usd": str(_recorded_provider_cost(output_dir)),
    }
    path = output_dir / "run_summary.json"
    path.write_text(canonical_json(summary) + "\n", encoding="utf-8", newline="\n")
    return summary


def _write_exclusions(
    output_dir: Path,
    cases: list[BenchmarkCase],
    manifest: RunManifest,
) -> ArtifactRef:
    excluded = [
        {
            "case_id": case.case_id,
            "split": case.split.value,
            "reason": "split_not_selected",
        }
        for case in cases
        if case.split not in manifest.splits
    ]
    return write_json_artifact(
        output_dir,
        "exclusions.json",
        {"schema_version": "1.0", "excluded": excluded},
    )


def _result_record_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)


def _recorded_provider_cost(output_dir: Path) -> Decimal:
    path = output_dir / "records" / "result_records.jsonl"
    if not path.exists():
        return Decimal("0")
    total = Decimal("0")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        payload = json.loads(line)
        provider = payload.get("provider")
        if isinstance(provider, dict):
            total += Decimal(str(provider.get("cost_usd", "0")))
    return total


def _work_item_id(
    run_id: str,
    case_id: str,
    approach: Approach,
    repetition: int,
    analysis_role: AnalysisRole | None = None,
) -> str:
    payload = {
        "run_id": run_id,
        "case_id": case_id,
        "approach": approach.value,
        "repetition": repetition,
    }
    if analysis_role is not None:
        payload["analysis_role"] = analysis_role.value
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
