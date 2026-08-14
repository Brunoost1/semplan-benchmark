from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from semplan.approaches.tool_agent import ToolAgentRunner, ToolExecutor
from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.contracts import ResultOutcome, ToolAgentTurnEnvelope
from semplan.errors import ErrorCode, ProjectError
from semplan.executor import CompiledSemanticQuery, SemanticExecutionResult
from semplan.normalizer import ReferenceContext
from semplan.prompts import PromptRegistry
from semplan.providers import FakeProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


def test_tool_agent_rejects_unknown_tool() -> None:
    with pytest.raises(ValidationError):
        ToolAgentTurnEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "tool_calls": [
                    {
                        "schema_version": "1.0",
                        "tool_name": "made_up",
                        "arguments": {},
                    }
                ],
                "final_request": None,
                "cannot_answer": False,
                "reason_code": None,
            }
        )


def test_tool_agent_rejects_extra_tool_arguments() -> None:
    with pytest.raises(ValidationError):
        ToolAgentTurnEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "tool_calls": [
                    {
                        "schema_version": "1.0",
                        "tool_name": "aggregate",
                        "arguments": {
                            "metrics": ["net_revenue"],
                            "dimensions": [],
                            "unknown": True,
                        },
                    }
                ],
                "final_request": None,
                "cannot_answer": False,
                "reason_code": None,
            }
        )


def test_describe_supported_fields_tool_is_deterministic() -> None:
    catalog = load_catalog(PROJECT_ROOT / "catalog")
    turn = ToolAgentTurnEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "tool_calls": [
                {
                    "schema_version": "1.0",
                    "tool_name": "describe_supported_fields",
                    "arguments": {"include_metrics": True, "include_dimensions": True},
                }
            ],
            "final_request": None,
            "cannot_answer": False,
            "reason_code": None,
        }
    )
    executor = ToolExecutor(
        catalog=catalog,
        reference_context=ReferenceContext(date(2026, 8, 1), "UTC"),
    )

    result = executor.execute(turn.tool_calls[0], call_index=0)

    assert result.execution is None
    assert result.call_record.result_digest is not None
    assert "net_revenue" in result.payload["metrics"]


def test_tool_agent_runner_enforces_call_limit() -> None:
    case = next(
        case for case in load_benchmark_cases(BENCHMARK_DIR) if case.expected_policy == "ALLOW"
    )
    provider = FakeProvider(
        {
            case.case_id: {
                "schema_version": "1.0",
                "tool_calls": [
                    {
                        "schema_version": "1.0",
                        "tool_name": "describe_supported_fields",
                        "arguments": {"include_metrics": True, "include_dimensions": True},
                    },
                    {
                        "schema_version": "1.0",
                        "tool_name": "describe_supported_fields",
                        "arguments": {"include_metrics": True, "include_dimensions": True},
                    },
                ],
                "final_request": None,
                "cannot_answer": False,
                "reason_code": None,
            }
        }
    )
    runner = ToolAgentRunner(
        provider=provider,
        catalog=load_catalog(PROJECT_ROOT / "catalog"),
        prompt_registry=PromptRegistry.load(PROJECT_ROOT / "prompts"),
        max_tool_calls=1,
    )

    with pytest.raises(ProjectError):
        runner.run_case(case)


def test_tool_agent_runner_wraps_invalid_provider_payload() -> None:
    case = next(
        case for case in load_benchmark_cases(BENCHMARK_DIR) if case.expected_policy == "ALLOW"
    )
    provider = FakeProvider(
        {
            case.case_id: {
                "schema_version": "1.0",
                "tool_calls": [
                    {
                        "schema_version": "1.0",
                        "tool_name": "made_up",
                        "arguments": {},
                    }
                ],
                "final_request": None,
                "cannot_answer": False,
                "reason_code": None,
            }
        }
    )
    runner = ToolAgentRunner(
        provider=provider,
        catalog=load_catalog(PROJECT_ROOT / "catalog"),
        prompt_registry=PromptRegistry.load(PROJECT_ROOT / "prompts"),
    )

    with pytest.raises(ProjectError) as exc_info:
        runner.run_case(case)

    assert exc_info.value.to_record().code is ErrorCode.OUTPUT_SCHEMA_INVALID


def test_tool_agent_runner_executes_final_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SemanticExecutionResult(
            outcome=ResultOutcome.ANSWERED,
            rows=[{"region": "North", "net_revenue": "10.00"}],
            units={"net_revenue": "usd"},
            compiled_query=CompiledSemanticQuery(
                sql="SELECT 1",
                guard_sql="SELECT 1",
                bind_params={},
                sql_sha256="sha256:" + ("a" * 64),
            ),
            row_count=1,
        )

    monkeypatch.setattr("semplan.approaches.tool_agent.runner.execute_semantic_plan", fake_execute)
    case = next(
        case for case in load_benchmark_cases(BENCHMARK_DIR) if case.expected_policy == "ALLOW"
    )
    provider = FakeProvider(
        {
            case.case_id: {
                "schema_version": "1.0",
                "tool_calls": [],
                "final_request": {
                    "schema_version": "1.0",
                    "operation": "REPLACE",
                    "intent": "grouped_metric",
                    "metrics": ["net_revenue"],
                    "dimensions": ["region"],
                    "filters": [{"field": "year", "operator": "EQ", "value": 2026}],
                    "time_grain": "year",
                    "sort": [{"field": "net_revenue", "direction": "desc"}],
                    "limit": 5,
                    "comparison": None,
                    "clarifications": [],
                    "out_of_scope_reason": None,
                    "confidence": "1",
                },
                "cannot_answer": False,
                "reason_code": None,
            }
        }
    )
    runner = ToolAgentRunner(
        provider=provider,
        catalog=load_catalog(PROJECT_ROOT / "catalog"),
        prompt_registry=PromptRegistry.load(PROJECT_ROOT / "prompts"),
    )

    result = runner.run_case(case)

    assert result.outcome is ResultOutcome.ANSWERED
    assert result.execution is not None


@pytest.mark.parametrize(
    "tool_call",
    [
        {
            "schema_version": "1.0",
            "tool_name": "aggregate",
            "arguments": {
                "metrics": ["net_revenue"],
                "dimensions": ["region"],
                "filters": [],
                "time_grain": "year",
                "sort": [],
                "limit": 5,
            },
        },
        {
            "schema_version": "1.0",
            "tool_name": "rank",
            "arguments": {
                "metrics": ["net_revenue"],
                "dimensions": ["region"],
                "filters": [],
                "time_grain": "year",
                "sort": [{"field": "net_revenue", "direction": "desc"}],
                "limit": 5,
            },
        },
        {
            "schema_version": "1.0",
            "tool_name": "compare_periods",
            "arguments": {
                "metric": "net_revenue",
                "dimensions": [],
                "filters": [],
                "time_grain": "month",
                "limit": 5,
            },
        },
        {
            "schema_version": "1.0",
            "tool_name": "compare_actual_budget",
            "arguments": {
                "dimensions": ["expense_category"],
                "filters": [],
                "limit": 5,
            },
        },
        {
            "schema_version": "1.0",
            "tool_name": "contract_status",
            "arguments": {
                "dimensions": ["contract_risk"],
                "filters": [{"field": "status", "operator": "EQ", "value": "active"}],
                "limit": 5,
            },
        },
    ],
)
def test_tool_executor_covers_analytics_tools(
    tool_call: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SemanticExecutionResult(
            outcome=ResultOutcome.ANSWERED,
            rows=[{"ok": True}],
            units={},
            compiled_query=CompiledSemanticQuery(
                sql="SELECT 1",
                guard_sql="SELECT 1",
                bind_params={},
                sql_sha256="sha256:" + ("d" * 64),
            ),
            row_count=1,
        )

    monkeypatch.setattr("semplan.approaches.tool_agent.runner.execute_semantic_plan", fake_execute)
    turn = ToolAgentTurnEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "tool_calls": [tool_call],
            "final_request": None,
            "cannot_answer": False,
            "reason_code": None,
        }
    )
    executor = ToolExecutor(
        catalog=load_catalog(PROJECT_ROOT / "catalog"),
        reference_context=ReferenceContext(date(2026, 8, 1), "UTC"),
    )

    result = executor.execute(turn.tool_calls[0], call_index=0)

    assert result.execution is not None
    assert result.call_record.validation_outcome == "accepted"


def test_tool_agent_runner_handles_cannot_answer() -> None:
    case = next(
        case for case in load_benchmark_cases(BENCHMARK_DIR) if case.case_id.startswith("ADV")
    )
    provider = FakeProvider(
        {
            case.case_id: {
                "schema_version": "1.0",
                "tool_calls": [],
                "final_request": None,
                "cannot_answer": True,
                "reason_code": "WRITE_OPERATION",
            }
        }
    )
    runner = ToolAgentRunner(
        provider=provider,
        catalog=load_catalog(PROJECT_ROOT / "catalog"),
        prompt_registry=PromptRegistry.load(PROJECT_ROOT / "prompts"),
    )

    result = runner.run_case(case)

    assert result.outcome is ResultOutcome.OUT_OF_SCOPE
    assert result.tool_results == []
