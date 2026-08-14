"""Manual one-case provider smoke workflow."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.approaches.direct_sql import DirectSqlRunner
from semplan.approaches.semantic_plan import SemanticPlanRunner, default_prompt_registry
from semplan.approaches.tool_agent import ToolAgentRunner
from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.catalog.models import Catalog
from semplan.contracts import (
    Approach,
    BenchmarkCase,
    CostEstimate,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderRequest,
    ProviderResponse,
    RunManifest,
)
from semplan.costs import BudgetController, BudgetLedger, load_price_table
from semplan.costs.pricing import ensure_price_table_fresh
from semplan.data_generation.writer import canonical_json
from semplan.errors import ErrorCode, ProjectError
from semplan.prompts import PromptRegistry
from semplan.providers import (
    BudgetedProvider,
    CachedProvider,
    ModelProvider,
    OpenAIProvider,
    ProviderCache,
)
from semplan.runtime_env import openai_api_key


def run_manual_paid_smoke(
    *,
    benchmark_dir: Path,
    case_id: str,
    approach: Approach,
    manifest_path: Path,
    price_table_path: Path,
    cache_dir: Path,
    output_dir: Path,
    allow_paid: bool,
    replay_only: bool,
    database_url: str | None = None,
) -> dict[str, object]:
    """Run or replay one owner-invoked OpenAI smoke case."""

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_run_manifest(manifest_path)
    price_table = load_price_table(price_table_path)
    catalog = load_catalog(Path("catalog"))
    prompts = default_prompt_registry()
    case = _load_case(benchmark_dir, case_id)
    cache = ProviderCache(cache_dir)

    budgeted_provider: BudgetedProvider | None = None
    provider: ModelProvider
    if replay_only:
        provider = CacheOnlyProvider(cache)
        provider_name = manifest.model.provider
    else:
        if not allow_paid:
            raise ProjectError(ErrorCode.BUDGET_EXCEEDED, "Manual smoke requires --allow-paid")
        if not manifest.allow_paid:
            raise ProjectError(
                ErrorCode.BUDGET_EXCEEDED,
                "Run manifest does not permit paid execution",
            )
        api_key = openai_api_key()
        if api_key is None:
            raise ProjectError(ErrorCode.CFG_INVALID, "OPENAI_API_KEY is required")
        ensure_price_table_fresh(price_table)
        ledger = BudgetLedger(output_dir / "budget_ledger.json")
        budget = BudgetController(
            run_manifest=manifest,
            price_table=price_table,
            allow_paid=allow_paid,
            api_key_present=api_key is not None,
            ledger=ledger,
            current_run_spend_usd=ledger.total_spend_usd(),
        )
        openai_provider = OpenAIProvider(
            price_table=price_table,
            allow_paid=allow_paid,
            api_key=api_key,
        )
        budgeted_provider = BudgetedProvider(openai_provider, budget)
        provider = CachedProvider(budgeted_provider, cache)
        provider_name = "openai"

    runner = _runner_for_approach(
        approach,
        provider=provider,
        catalog=catalog,
        prompts=prompts,
        database_url=database_url,
        provider_name=provider_name,
        model_name=manifest.model.id,
        inference_parameters=_manifest_inference_parameters(manifest),
    )
    result = runner.run_case(case)
    report: dict[str, object] = {
        "schema_version": "1.0",
        "status": "passed",
        "replay_only": replay_only,
        "approach": approach.value,
        "case_id": case.case_id,
        "model": manifest.model.id,
        "provider_response_id": result.provider_response.response_id,
        "provider_finish_status": result.provider_response.finish_status.value,
        "outcome": result.outcome.value,
        "incremental_paid_calls": 0
        if replay_only
        else len(budgeted_provider.preflight_checks)
        if budgeted_provider is not None
        else 0,
        "preflight_checks": [
            check.model_dump(mode="json")
            for check in (budgeted_provider.preflight_checks if budgeted_provider else [])
        ],
        "provider_response": result.provider_response.model_dump(mode="json"),
        "canonical_response": result.response.model_dump(mode="json"),
    }
    (output_dir / "manual_paid_smoke_report.json").write_text(
        canonical_json(report) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


class CacheOnlyProvider:
    """Provider implementation that only replays completed cache entries."""

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


def _load_run_manifest(path: Path) -> RunManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Run manifest cannot be loaded",
            detail={"path": str(path), "reason": str(exc)},
        ) from exc
    return RunManifest.model_validate(raw)


def _load_case(benchmark_dir: Path, case_id: str) -> BenchmarkCase:
    for case in load_benchmark_cases(benchmark_dir):
        if case.case_id == case_id:
            return case
    raise ProjectError(
        ErrorCode.CFG_INVALID,
        "Benchmark case not found",
        detail={"case_id": case_id},
    )


def _runner_for_approach(
    approach: Approach,
    *,
    provider: ModelProvider,
    catalog: Catalog,
    prompts: PromptRegistry,
    database_url: str | None,
    provider_name: str,
    model_name: str,
    inference_parameters: dict[str, object] | None = None,
) -> Any:
    if approach is Approach.A1:
        return DirectSqlRunner(
            provider=provider,
            catalog=catalog,
            prompt_registry=prompts,
            database_url=database_url,
            provider_name=provider_name,
            model_name=model_name,
            inference_parameters=inference_parameters,
        )
    if approach is Approach.A2:
        return ToolAgentRunner(
            provider=provider,
            catalog=catalog,
            prompt_registry=prompts,
            database_url=database_url,
            provider_name=provider_name,
            model_name=model_name,
            inference_parameters=inference_parameters,
        )
    return SemanticPlanRunner(
        approach=approach,
        provider=provider,
        catalog=catalog,
        prompt_registry=prompts,
        database_url=database_url,
        provider_name=provider_name,
        model_name=model_name,
        inference_parameters=inference_parameters,
    )


def _manifest_inference_parameters(manifest: RunManifest) -> dict[str, object]:
    parameters: dict[str, object] = dict(manifest.model.parameters)
    if manifest.model.provider == "openai":
        parameters.setdefault("reasoning_effort", manifest.model.reasoning_effort)
    return parameters
