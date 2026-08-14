"""Threaded paid-run executor for resumable F7 scientific execution."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.contracts import (
    Approach,
    CostEstimate,
    ExperimentMode,
    PriceTable,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    ResultError,
    RunManifest,
    WorkItemStatus,
)
from semplan.costs import BudgetLedger, load_price_table
from semplan.costs.pricing import (
    actual_response_cost,
    ensure_price_table_fresh,
    estimate_request_cost,
)
from semplan.errors import ErrorCode, ProjectError
from semplan.experiments.artifacts import rebuild_result_jsonl, validate_experiment_directory
from semplan.experiments.manifest import (
    copy_manifest_for_run,
    load_run_manifest,
    manifest_file_hash,
    validate_manifest_for_execution,
)
from semplan.experiments.reporting import generate_analysis_artifacts
from semplan.experiments.runner import (
    CapturingProvider,
    WorkItem,
    _failure_status,
    _fake_provider_factory,
    _is_completed,
    _load_or_create_ledger,
    _mark_ledger,
    _record_failure,
    _record_success,
    _runner_for_item,
    _terminal_status,
    _write_exclusions,
    _write_run_summary,
    create_work_items,
)
from semplan.prompts import PromptRegistry
from semplan.providers import CachedProvider, ModelProvider, OpenAIProvider, ProviderCache
from semplan.runtime_env import openai_api_key


class ParallelBudgetedProvider:
    """Budget wrapper with in-flight reservations for threaded provider calls."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        manifest: RunManifest,
        price_table: PriceTable,
        ledger: BudgetLedger,
        lock: threading.Lock,
        reserved: list[Decimal],
        safety_multiplier: Decimal = Decimal("1.20"),
    ) -> None:
        self.provider = provider
        self.manifest = manifest
        self.price_table = price_table
        self.ledger = ledger
        self.lock = lock
        self.reserved = reserved
        self.safety_multiplier = safety_multiplier

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        estimate = estimate_request_cost(
            request,
            self.price_table,
            safety_multiplier=self.safety_multiplier,
        ).estimated_usd
        with self.lock:
            current_spend = self.ledger.total_spend_usd()
            projected = current_spend + self.reserved[0] + estimate
            if projected > self.manifest.budget_usd:
                raise ProjectError(
                    ErrorCode.BUDGET_EXCEEDED,
                    "Parallel in-flight budget reservation would exceed run budget",
                    detail={
                        "current_spend_usd": str(current_spend),
                        "reserved_usd": str(self.reserved[0]),
                        "estimate_usd": str(estimate),
                        "run_budget_usd": str(self.manifest.budget_usd),
                    },
                )
            self.reserved[0] += estimate
        try:
            response = self.provider.complete(request)
        except Exception:
            with self.lock:
                self.reserved[0] -= estimate
            raise
        actual = actual_response_cost(response, self.price_table)
        response = response.model_copy(update={"cost": actual})
        with self.lock:
            self.reserved[0] -= estimate
            current_spend = self.ledger.total_spend_usd()
            if current_spend + actual.estimated_usd > self.manifest.budget_usd:
                raise ProjectError(
                    ErrorCode.BUDGET_EXCEEDED,
                    "Actual spend would exceed run budget",
                    detail={
                        "current_spend_usd": str(current_spend),
                        "actual_cost_usd": str(actual.estimated_usd),
                        "run_budget_usd": str(self.manifest.budget_usd),
                    },
                )
            self.ledger.append_response(response, actual.estimated_usd)
        return response

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        with self.lock:
            cached_spend = self.ledger.total_spend_usd()
            estimate = estimate_request_cost(
                request,
                self.price_table,
                safety_multiplier=self.safety_multiplier,
            )
            if cached_spend + self.reserved[0] + estimate.estimated_usd > self.manifest.budget_usd:
                raise ProjectError(
                    ErrorCode.BUDGET_EXCEEDED,
                    "Parallel estimate would exceed run budget",
                )
            return estimate

    def healthcheck(self) -> ProviderHealth:
        return self.provider.healthcheck()


def run_experiment_parallel(
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
    workers: int = 4,
    progress_interval_seconds: int = 60,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run planned work items with bounded parallel provider dispatch."""

    if workers < 1:
        raise ProjectError(ErrorCode.CFG_INVALID, "workers must be positive")
    manifest = load_run_manifest(manifest_path)
    if manifest.mode is ExperimentMode.BATCH:
        raise ProjectError(ErrorCode.CFG_INVALID, "Batch execution is not implemented")
    preflight = validate_manifest_for_execution(
        manifest,
        benchmark_dir=benchmark_dir,
        allow_paid=allow_paid,
    )
    price_table = _load_execution_price_table(manifest, price_table_path)
    copy_manifest_for_run(manifest_path, output_dir)
    cases = load_benchmark_cases(benchmark_dir)
    case_map = {case.case_id: case for case in cases if case.split in manifest.splits}
    work_items = create_work_items(manifest, list(case_map.values()))
    _write_exclusions(output_dir, cases, manifest)
    ledger = _load_or_create_ledger(output_dir, manifest, work_items, resume=resume)
    if manifest.mode is ExperimentMode.DRY_RUN:
        return _write_run_summary(
            output_dir,
            manifest=manifest,
            status="dry_run",
            preflight=preflight,
            executed_count=0,
            work_item_count=len(work_items),
        )

    provider_factory = _parallel_provider_factory(
        benchmark_dir=benchmark_dir,
        output_dir=output_dir,
        manifest=manifest,
        allow_paid=allow_paid,
        price_table=price_table,
        cache_dir=cache_dir,
    )
    catalog = load_catalog(Path("catalog"))
    prompts = PromptRegistry.load(Path("prompts"))
    state_lock = threading.Lock()
    pending_items = _pending_work_items(output_dir, ledger, work_items)
    if max_items is not None:
        pending_items = pending_items[:max_items]
    progress_callback = progress_callback or (lambda _event: None)
    executed_count = 0
    interrupted = max_items is not None and len(pending_items) < len(
        _pending_work_items(output_dir, ledger, work_items)
    )

    def run_one(item: WorkItem) -> None:
        nonlocal executed_count
        with state_lock:
            ledger_item = ledger["work_items"][item.work_item_id]
            if _is_completed(output_dir, ledger_item):
                return
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
            with state_lock:
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
                executed_count += 1
        except ProjectError as exc:
            with state_lock:
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

    _run_thread_pool(
        pending_items,
        run_one,
        workers=workers,
        progress_interval_seconds=progress_interval_seconds,
        progress_callback=lambda: progress_callback(
            _progress_event(output_dir, ledger, executed_count, len(pending_items))
        ),
    )
    with state_lock:
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


def _run_thread_pool(
    items: list[WorkItem],
    run_one: Callable[[WorkItem], None],
    *,
    workers: int,
    progress_interval_seconds: int,
    progress_callback: Callable[[], None],
) -> None:
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: set[Future[None]] = {executor.submit(run_one, item) for item in items}
    try:
        last_progress = time.monotonic()
        while futures:
            done, futures = wait(
                futures,
                timeout=max(1, progress_interval_seconds),
                return_when=FIRST_COMPLETED,
            )
            if not done and time.monotonic() - last_progress >= progress_interval_seconds:
                progress_callback()
                last_progress = time.monotonic()
            for future in done:
                future.result()
            if done and time.monotonic() - last_progress >= progress_interval_seconds:
                progress_callback()
                last_progress = time.monotonic()
    except BaseException:
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    executor.shutdown(wait=True)


def _pending_work_items(
    output_dir: Path,
    ledger: dict[str, Any],
    work_items: list[WorkItem],
) -> list[WorkItem]:
    pending: list[WorkItem] = []
    for item in work_items:
        ledger_item = ledger["work_items"][item.work_item_id]
        if not _is_completed(output_dir, ledger_item):
            pending.append(item)
    return pending


def _progress_event(
    output_dir: Path,
    ledger: dict[str, Any],
    executed_count: int,
    invocation_item_count: int,
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for item in ledger["work_items"].values():
        status = str(item.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "event": "parallel_progress",
        "executed_this_invocation": executed_count,
        "invocation_item_count": invocation_item_count,
        "ledger_counts": status_counts,
        "actual_api_spend_usd": str(_budget_spend(output_dir)),
    }


def _load_execution_price_table(
    manifest: RunManifest,
    price_table_path: Path | None,
) -> PriceTable | None:
    if not (manifest.allow_paid or manifest.model.provider == "openai"):
        return None
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
            detail={"expected": manifest.price_table_sha256, "actual": actual_price_hash},
        )
    price_table = load_price_table(price_table_path)
    ensure_price_table_fresh(price_table)
    return price_table


def _parallel_provider_factory(
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
        raise ProjectError(ErrorCode.CFG_INVALID, "Parallel replay is not supported")
    if not manifest.allow_paid or not allow_paid:
        raise ProjectError(ErrorCode.BUDGET_EXCEEDED, "OpenAI experiment requires --allow-paid")
    if price_table is None:
        raise ProjectError(ErrorCode.CFG_INVALID, "OpenAI experiment requires a price table")
    api_key = openai_api_key()
    if api_key is None:
        raise ProjectError(ErrorCode.CFG_INVALID, "OPENAI_API_KEY is required")
    budget_lock = threading.Lock()
    reserved = [Decimal("0")]
    budgeted_provider = ParallelBudgetedProvider(
        OpenAIProvider(price_table=price_table, allow_paid=allow_paid, api_key=api_key),
        manifest=manifest,
        price_table=price_table,
        ledger=BudgetLedger(output_dir / "budget_ledger.json"),
        lock=budget_lock,
        reserved=reserved,
    )
    cached_provider = CachedProvider(
        budgeted_provider,
        ProviderCache(cache_dir or output_dir / "provider_cache"),
    )
    return lambda _approach: cached_provider


def _budget_spend(output_dir: Path) -> Decimal:
    path = output_dir / "budget_ledger.json"
    if not path.exists():
        return Decimal("0")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return sum(
        (Decimal(str(entry.get("cost_usd", "0"))) for entry in payload.get("entries", [])),
        Decimal("0"),
    )
