"""A2 typed tool-agent baseline orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from semplan.catalog.models import Catalog
from semplan.contracts import (
    AggregateToolCall,
    Approach,
    BenchmarkCase,
    CanonicalResponse,
    CompareActualBudgetToolCall,
    ComparePeriodsToolCall,
    ComparisonSpec,
    ContractStatusToolCall,
    DescribeSupportedFieldsToolCall,
    Direction,
    Intent,
    LocalizedText,
    Operation,
    OutOfScopeResponse,
    ProviderFinishStatus,
    ProviderResponse,
    RankToolCall,
    ResultOutcome,
    SemanticRequestEnvelope,
    SortSpec,
    ToolAgentToolCall,
    ToolAgentTurnEnvelope,
    ToolCallRecord,
    ToolValidationOutcome,
)
from semplan.data_generation.writer import canonical_json
from semplan.errors import ErrorCode, ProjectError
from semplan.executor import SemanticExecutionResult, execute_semantic_plan
from semplan.normalizer import ReferenceContext, normalize_semantic_request
from semplan.prompts import PromptRegistry
from semplan.providers import ModelProvider, build_provider_request
from semplan.reporting import (
    response_from_clarification,
    response_from_execution,
    response_from_out_of_scope,
)

MAX_TOOL_CALLS = 4


@dataclass(frozen=True)
class ToolExecutionResult:
    call_record: ToolCallRecord
    execution: SemanticExecutionResult | None
    payload: dict[str, object]


@dataclass(frozen=True)
class ToolAgentRunResult:
    approach: Approach
    case_id: str
    outcome: ResultOutcome
    provider_response: ProviderResponse
    turn: ToolAgentTurnEnvelope
    tool_results: list[ToolExecutionResult]
    execution: SemanticExecutionResult | None
    response: CanonicalResponse


class ToolExecutor:
    """Execute the closed set of generic analytics tools."""

    def __init__(
        self,
        *,
        catalog: Catalog,
        reference_context: ReferenceContext,
        database_url: str | None = None,
    ) -> None:
        self.catalog = catalog
        self.reference_context = reference_context
        self.database_url = database_url

    def execute(self, call: ToolAgentToolCall, call_index: int) -> ToolExecutionResult:
        started = time.perf_counter()
        request = self._semantic_request_for_call(call)
        if request is None:
            payload: dict[str, object] = {}
            if isinstance(call, DescribeSupportedFieldsToolCall):
                if call.arguments.include_metrics:
                    payload["metrics"] = sorted(self.catalog.metrics)
                if call.arguments.include_dimensions:
                    payload["dimensions"] = sorted(self.catalog.dimensions)
            digest = _digest(payload)
            return ToolExecutionResult(
                call_record=ToolCallRecord(
                    schema_version="1.0",
                    tool_name=call.tool_name,
                    arguments=call.arguments.model_dump(mode="json"),
                    call_index=call_index,
                    validation_outcome=ToolValidationOutcome.ACCEPTED,
                    result_digest=digest,
                    duration_ms=_duration_ms(started),
                ),
                execution=None,
                payload=payload,
            )

        try:
            normalized = normalize_semantic_request(request, self.catalog, self.reference_context)
        except ValidationError as exc:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Tool call normalization failed contract validation",
                detail={"schema": "SemanticRequestEnvelope", "error_count": len(exc.errors())},
            ) from exc
        if normalized.outcome is not ResultOutcome.ANSWERED or normalized.plan is None:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Tool call did not normalize to an executable plan",
                detail={"tool_name": call.tool_name},
            )
        execution = execute_semantic_plan(
            normalized.plan,
            self.catalog,
            database_url=self.database_url,
        )
        payload = {
            "rows": execution.rows,
            "row_count": execution.row_count,
            "sql_sha256": execution.compiled_query.sql_sha256,
        }
        digest = _digest(payload)
        return ToolExecutionResult(
            call_record=ToolCallRecord(
                schema_version="1.0",
                tool_name=call.tool_name,
                arguments=call.arguments.model_dump(mode="json"),
                call_index=call_index,
                validation_outcome=ToolValidationOutcome.ACCEPTED,
                result_digest=digest,
                duration_ms=_duration_ms(started),
            ),
            execution=execution,
            payload=payload,
        )

    def _semantic_request_for_call(
        self,
        call: ToolAgentToolCall,
    ) -> SemanticRequestEnvelope | None:
        if isinstance(call, DescribeSupportedFieldsToolCall):
            return None
        if isinstance(call, AggregateToolCall | RankToolCall):
            aggregate_args = call.arguments
            return SemanticRequestEnvelope(
                schema_version="1.0",
                operation=Operation.REPLACE,
                intent=Intent.RANKING if isinstance(call, RankToolCall) else Intent.GROUPED_METRIC,
                metrics=aggregate_args.metrics,
                dimensions=aggregate_args.dimensions,
                filters=aggregate_args.filters,
                time_grain=aggregate_args.time_grain,
                sort=aggregate_args.sort,
                limit=aggregate_args.limit,
                comparison=None,
                clarifications=[],
                confidence=Decimal("1"),
            )
        if isinstance(call, ComparePeriodsToolCall):
            period_args = call.arguments
            dimensions = period_args.dimensions or [period_args.time_grain.value]
            return SemanticRequestEnvelope(
                schema_version="1.0",
                operation=Operation.REPLACE,
                intent=Intent.COMPARISON,
                metrics=[period_args.metric],
                dimensions=dimensions,
                filters=period_args.filters,
                time_grain=period_args.time_grain,
                sort=[],
                limit=period_args.limit,
                comparison=ComparisonSpec(
                    mode="period_over_period",
                    baseline="previous_period",
                ),
                clarifications=[],
                confidence=Decimal("1"),
            )
        if isinstance(call, CompareActualBudgetToolCall):
            budget_args = call.arguments
            return SemanticRequestEnvelope(
                schema_version="1.0",
                operation=Operation.REPLACE,
                intent=Intent.COMPARISON,
                metrics=["expense_amount", "budget_amount", "budget_variance"],
                dimensions=budget_args.dimensions,
                filters=budget_args.filters,
                time_grain=None,
                sort=[SortSpec(field="budget_variance", direction=Direction.DESC)],
                limit=budget_args.limit,
                comparison=ComparisonSpec(mode="period_over_period", baseline="budget"),
                clarifications=[],
                confidence=Decimal("1"),
            )
        if isinstance(call, ContractStatusToolCall):
            contract_args = call.arguments
            return SemanticRequestEnvelope(
                schema_version="1.0",
                operation=Operation.REPLACE,
                intent=Intent.GROUPED_METRIC,
                metrics=["active_contract_value"],
                dimensions=contract_args.dimensions,
                filters=contract_args.filters,
                time_grain=None,
                sort=[SortSpec(field="active_contract_value", direction=Direction.DESC)],
                limit=contract_args.limit,
                comparison=None,
                clarifications=[],
                confidence=Decimal("1"),
            )
        raise ProjectError(
            ErrorCode.OUTPUT_SCHEMA_INVALID,
            "Unknown A2 tool",
            detail={"tool_name": call.tool_name},
        )


class ToolAgentRunner:
    """Run A2 through a strict tool-turn output and deterministic tools."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        catalog: Catalog,
        prompt_registry: PromptRegistry,
        database_url: str | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
        provider_name: str = "fake",
        model_name: str = "fake-tool-agent-v1",
        inference_parameters: dict[str, object] | None = None,
        request_metadata: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        self.catalog = catalog
        self.prompt = prompt_registry.for_approach(Approach.A2)
        self.database_url = database_url
        self.max_tool_calls = max_tool_calls
        self.provider_name = provider_name
        self.model_name = model_name
        self.inference_parameters = dict(inference_parameters or {"temperature": "0"})
        self.request_metadata = dict(request_metadata or {})

    def run_case(self, case: BenchmarkCase) -> ToolAgentRunResult:
        context = ReferenceContext(case.context.reference_date, case.context.timezone)
        prompt_text = self.prompt.render(
            {
                "locale": case.language.value,
                "reference_date": case.context.reference_date.isoformat(),
                "utterance": case.utterance,
                "catalog_summary": _catalog_summary(self.catalog),
                "tool_summary": _tool_summary(),
            }
        )
        provider_request = build_provider_request(
            provider=self.provider_name,
            model=self.model_name,
            prompt_id=self.prompt.metadata.prompt_id,
            prompt_sha256=self.prompt.sha256,
            system=prompt_text,
            inputs=[case.utterance],
            output_schema_ref=self.prompt.metadata.expected_output_schema,
            output_schema_sha256=self.prompt.output_schema_sha256,
            inference_parameters=self.inference_parameters,
            timeout_seconds=30,
            metadata={
                "approach": Approach.A2.value,
                "case_id": case.case_id,
                "prompt_sha256": self.prompt.sha256,
                **self.request_metadata,
            },
        )
        provider_response = self.provider.complete(provider_request)
        if provider_response.finish_status is not ProviderFinishStatus.STOP:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Provider did not return a completed tool-agent response",
                detail={"finish_status": provider_response.finish_status.value},
            )
        try:
            turn = ToolAgentTurnEnvelope.model_validate(provider_response.parsed_payload)
        except ValidationError as exc:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Provider tool-agent payload failed schema validation",
                detail={"schema": "ToolAgentTurnEnvelope", "error_count": len(exc.errors())},
            ) from exc
        if len(turn.tool_calls) > self.max_tool_calls:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Tool-agent response exceeds configured tool-call limit",
                detail={"tool_calls": len(turn.tool_calls), "max_tool_calls": self.max_tool_calls},
            )
        if turn.cannot_answer:
            assert turn.reason_code is not None
            response = response_from_out_of_scope(
                OutOfScopeResponse(
                    reason_code=turn.reason_code,
                    message=LocalizedText(
                        **{
                            "en-US": "The request cannot be answered safely by the tool agent.",
                            "pt-BR": (
                                "A solicitacao nao pode ser respondida com seguranca pelo agente."
                            ),
                        }
                    ),
                )
            )
            return ToolAgentRunResult(
                approach=Approach.A2,
                case_id=case.case_id,
                outcome=ResultOutcome.OUT_OF_SCOPE,
                provider_response=provider_response,
                turn=turn,
                tool_results=[],
                execution=None,
                response=response,
            )

        executor = ToolExecutor(
            catalog=self.catalog,
            reference_context=context,
            database_url=self.database_url,
        )
        tool_results = [
            executor.execute(call, call_index=index) for index, call in enumerate(turn.tool_calls)
        ]

        execution: SemanticExecutionResult | None = None
        if turn.final_request is not None:
            try:
                normalized = normalize_semantic_request(turn.final_request, self.catalog, context)
            except ValidationError as exc:
                raise ProjectError(
                    ErrorCode.OUTPUT_SCHEMA_INVALID,
                    "Tool-agent final request normalization failed contract validation",
                    detail={
                        "schema": "SemanticRequestEnvelope",
                        "error_count": len(exc.errors()),
                    },
                ) from exc
            if normalized.outcome is ResultOutcome.ANSWERED:
                if normalized.plan is None:
                    raise ProjectError(ErrorCode.CFG_INVALID, "A2 final answer is missing plan")
                execution = execute_semantic_plan(
                    normalized.plan,
                    self.catalog,
                    database_url=self.database_url,
                )
                response = response_from_execution(execution)
            elif (
                normalized.outcome is ResultOutcome.CLARIFY and normalized.clarification is not None
            ):
                response = response_from_clarification(normalized.clarification)
            elif (
                normalized.outcome is ResultOutcome.OUT_OF_SCOPE
                and normalized.out_of_scope is not None
            ):
                response = response_from_out_of_scope(normalized.out_of_scope)
            else:
                raise ProjectError(ErrorCode.CFG_INVALID, "Invalid A2 final normalization outcome")
        elif tool_results and tool_results[-1].execution is not None:
            execution = tool_results[-1].execution
            response = response_from_execution(execution)
        else:
            response = CanonicalResponse(
                schema_version="1.0",
                outcome=ResultOutcome.ANSWERED,
                rows=[],
                units={},
                message=LocalizedText(
                    **{
                        "en-US": "Returned supported fields.",
                        "pt-BR": "Retornou os campos suportados.",
                    }
                ),
            )
        return ToolAgentRunResult(
            approach=Approach.A2,
            case_id=case.case_id,
            outcome=response.outcome,
            provider_response=provider_response,
            turn=turn,
            tool_results=tool_results,
            execution=execution,
            response=response,
        )


def _catalog_summary(catalog: Catalog) -> str:
    return (
        "metrics: "
        + ", ".join(sorted(catalog.metrics))
        + "\ndimensions: "
        + ", ".join(sorted(catalog.dimensions))
    )


def _tool_summary() -> str:
    return (
        "aggregate(metrics, dimensions, filters, time_grain, sort, limit)\n"
        "rank(metrics, dimensions, filters, time_grain, sort, limit)\n"
        "compare_periods(metric, dimensions, filters, time_grain, limit)\n"
        "compare_actual_budget(dimensions, filters, limit)\n"
        "contract_status(dimensions, filters, limit)\n"
        "describe_supported_fields(include_metrics, include_dimensions)"
    )


def _digest(payload: object) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _duration_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
