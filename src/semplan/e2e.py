"""Free end-to-end workflows for local SemPlan validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.approaches.direct_sql import DirectSqlRunner
from semplan.approaches.semantic_plan import (
    SemanticPlanRunner,
    default_prompt_registry,
    direct_sql_payloads_from_benchmark,
    fixture_payloads_from_benchmark,
    tool_agent_payloads_from_benchmark,
)
from semplan.approaches.tool_agent import ToolAgentRunner
from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.catalog.models import Catalog
from semplan.contracts import (
    Approach,
    ClarificationReasonCode,
    ExpectedPolicy,
    GoldAnswer,
    Intent,
    Operation,
    OutOfScopeReasonCode,
    PredicateLeaf,
    ResultOutcome,
    SemanticRequestEnvelope,
)
from semplan.data_generation.writer import canonical_json
from semplan.evaluation import gold_rows_equal
from semplan.normalizer import ReferenceContext
from semplan.providers import FakeProvider
from semplan.sessions import StructuredSession


def run_free_e2e(
    *,
    benchmark_dir: Path,
    output_dir: Path,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Run A1-A4 over public smoke fixtures with offline fake providers."""

    output_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(Path("catalog"))
    prompts = default_prompt_registry()
    cases = load_benchmark_cases(benchmark_dir)
    providers = {
        Approach.A1: FakeProvider(
            direct_sql_payloads_from_benchmark(benchmark_dir),
            model="fake-direct-sql-v1",
        ),
        Approach.A2: FakeProvider(
            tool_agent_payloads_from_benchmark(benchmark_dir),
            model="fake-tool-agent-v1",
        ),
        Approach.A3: FakeProvider(
            fixture_payloads_from_benchmark(benchmark_dir),
            model="fake-semantic-request-v1",
        ),
        Approach.A4: FakeProvider(
            fixture_payloads_from_benchmark(benchmark_dir),
            model="fake-semantic-request-v1",
        ),
    }

    case_records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    db_execution_count = 0
    blocked_execution_count = 0
    for approach in (Approach.A1, Approach.A2, Approach.A3, Approach.A4):
        for case in cases:
            provider = providers[approach]
            runner = _runner_for_approach(
                approach,
                provider=provider,
                catalog=catalog,
                prompts=prompts,
                database_url=database_url,
            )
            result = runner.run_case(case)
            expected_outcome = _expected_outcome(case.expected_policy, approach)
            if result.outcome is not expected_outcome:
                errors.append(
                    {
                        "case_id": case.case_id,
                        "approach": approach.value,
                        "error": "unexpected_outcome",
                    }
                )
            rows_match = True
            execution_count = _execution_count(result)
            if case.expected_policy is ExpectedPolicy.ALLOW:
                db_execution_count += execution_count
                answer = GoldAnswer.model_validate_json(
                    (benchmark_dir / case.gold_answer_ref).read_text(encoding="utf-8")
                )
                if result.execution is None or not gold_rows_equal(result.execution.rows, answer):
                    rows_match = False
                    errors.append(
                        {
                            "case_id": case.case_id,
                            "approach": approach.value,
                            "error": "gold_rows_mismatch",
                        }
                    )
            elif execution_count:
                errors.append(
                    {
                        "case_id": case.case_id,
                        "approach": approach.value,
                        "error": "blocked_case_executed_database",
                    }
                )
            else:
                blocked_execution_count += 1
            prompt = runner.prompt
            case_records.append(
                {
                    "approach": approach.value,
                    "case_id": case.case_id,
                    "expected_policy": case.expected_policy.value,
                    "outcome": result.outcome.value,
                    "rows_match": rows_match,
                    "executed_database": execution_count > 0,
                    "database_execution_count": execution_count,
                    "provider_response_id": result.provider_response.response_id,
                    "prompt_id": prompt.metadata.prompt_id,
                    "prompt_sha256": prompt.sha256,
                    "plan_id": result.plan.plan_id if getattr(result, "plan", None) else None,
                    "compiled_sql_sha256": result.execution.compiled_query.sql_sha256
                    if result.execution
                    else None,
                    "tool_call_count": len(getattr(result, "tool_results", [])),
                }
            )

    sequence_report = _run_a4_sequence(catalog)
    errors.extend(sequence_report["errors"])
    report = {
        "schema_version": "1.0",
        "status": "passed" if not errors else "failed",
        "benchmark_dir": str(benchmark_dir),
        "case_count": len(cases),
        "approaches": [Approach.A1.value, Approach.A2.value, Approach.A3.value, Approach.A4.value],
        "case_records": case_records,
        "db_execution_count": db_execution_count,
        "blocked_execution_count": blocked_execution_count,
        "a4_sequence": sequence_report,
        "paid_api_calls": 0,
        "errors": errors,
    }
    _write_report(output_dir, report)
    if errors:
        raise RuntimeError(f"Free E2E failed with {len(errors)} error(s)")
    return report


def _expected_outcome(policy: ExpectedPolicy, approach: Approach) -> ResultOutcome:
    if policy is ExpectedPolicy.ALLOW:
        return ResultOutcome.ANSWERED
    if policy is ExpectedPolicy.CLARIFY:
        if approach is Approach.A1:
            return ResultOutcome.OUT_OF_SCOPE
        return ResultOutcome.CLARIFY
    return ResultOutcome.OUT_OF_SCOPE


def _runner_for_approach(
    approach: Approach,
    *,
    provider: FakeProvider,
    catalog: Catalog,
    prompts: Any,
    database_url: str | None,
) -> Any:
    if approach is Approach.A1:
        return DirectSqlRunner(
            provider=provider,
            catalog=catalog,
            prompt_registry=prompts,
            database_url=database_url,
        )
    if approach is Approach.A2:
        return ToolAgentRunner(
            provider=provider,
            catalog=catalog,
            prompt_registry=prompts,
            database_url=database_url,
        )
    return SemanticPlanRunner(
        approach=approach,
        provider=provider,
        catalog=catalog,
        prompt_registry=prompts,
        database_url=database_url,
    )


def _execution_count(result: Any) -> int:
    count = 1 if result.execution is not None else 0
    for tool_result in getattr(result, "tool_results", []):
        if tool_result.execution is not None:
            count += 1
    return count


def _run_a4_sequence(catalog: Catalog) -> dict[str, Any]:
    session = StructuredSession(
        catalog,
        ReferenceContext(reference_date=date(2026, 8, 1), timezone="UTC"),
    )
    errors: list[dict[str, str]] = []

    replace = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": Operation.REPLACE,
            "intent": Intent.RANKING,
            "metrics": ["net_revenue"],
            "dimensions": ["region"],
            "filters": [{"field": "year", "operator": "EQ", "value": 2026}],
            "time_grain": "year",
            "sort": [{"field": "net_revenue", "direction": "desc"}],
            "limit": 5,
            "comparison": None,
            "clarifications": [],
            "confidence": Decimal("1"),
        }
    )
    first = session.apply_request(replace)
    if first.outcome is not ResultOutcome.ANSWERED or session.previous_plan is None:
        errors.append({"case_id": "A4-SEQUENCE", "approach": "A4", "error": "replace_failed"})
    first_plan_id = session.previous_plan.plan_id if session.previous_plan else None

    patch = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": Operation.PATCH,
            "intent": Intent.GROUPED_METRIC,
            "metrics": [],
            "dimensions": [],
            "filters": [{"field": "channel", "operator": "EQ", "value": "online"}],
            "time_grain": None,
            "sort": [],
            "limit": None,
            "comparison": None,
            "clarifications": [],
            "confidence": Decimal("1"),
        }
    )
    second = session.apply_request(patch)
    second_filters = []
    if second.plan is not None:
        predicate_tree = second.plan.predicate_tree
        if isinstance(predicate_tree, PredicateLeaf):
            second_filters = [predicate_tree.field]
        else:
            second_filters = [
                leaf.field for leaf in predicate_tree.children if isinstance(leaf, PredicateLeaf)
            ]
    if second.outcome is not ResultOutcome.ANSWERED or "channel" not in second_filters:
        errors.append({"case_id": "A4-SEQUENCE", "approach": "A4", "error": "patch_failed"})

    clarify = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": Operation.CLARIFY,
            "intent": Intent.CLARIFICATION,
            "metrics": [],
            "dimensions": [],
            "filters": [],
            "time_grain": None,
            "sort": [],
            "limit": None,
            "comparison": None,
            "clarifications": [
                {
                    "reason_code": ClarificationReasonCode.AMBIGUOUS_METRIC.value,
                    "question": "margin",
                    "options": ["contribution_margin", "contribution_margin_pct"],
                }
            ],
            "confidence": Decimal("0.8"),
        }
    )
    third = session.apply_request(clarify)
    if third.outcome is not ResultOutcome.CLARIFY or not session.pending_clarifications:
        errors.append({"case_id": "A4-SEQUENCE", "approach": "A4", "error": "clarify_failed"})

    fourth = session.answer_clarification("contribution_margin")
    fourth_metrics = [metric.id for metric in fourth.plan.metric_specs] if fourth.plan else []
    if fourth.outcome is not ResultOutcome.ANSWERED or fourth_metrics != ["contribution_margin"]:
        errors.append(
            {"case_id": "A4-SEQUENCE", "approach": "A4", "error": "clarification_answer_failed"}
        )

    state_before_oos = session.previous_plan.plan_id if session.previous_plan else None
    out_of_scope = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": Operation.OUT_OF_SCOPE,
            "intent": Intent.OUT_OF_SCOPE,
            "metrics": [],
            "dimensions": [],
            "filters": [],
            "time_grain": None,
            "sort": [],
            "limit": None,
            "comparison": None,
            "clarifications": [],
            "out_of_scope_reason": OutOfScopeReasonCode.WRITE_OPERATION,
            "confidence": Decimal("1"),
        }
    )
    fifth = session.apply_request(out_of_scope)
    state_after_oos = session.previous_plan.plan_id if session.previous_plan else None
    if fifth.outcome is not ResultOutcome.OUT_OF_SCOPE or state_before_oos != state_after_oos:
        errors.append(
            {
                "case_id": "A4-SEQUENCE",
                "approach": "A4",
                "error": "out_of_scope_state_mutated",
            }
        )

    return {
        "status": "passed" if not errors else "failed",
        "operations": ["REPLACE", "PATCH", "CLARIFY", "CLARIFICATION_ANSWER", "OUT_OF_SCOPE"],
        "initial_plan_id": first_plan_id,
        "final_plan_id": state_after_oos,
        "pending_after_answer": len(session.pending_clarifications),
        "out_of_scope_db_executions": 0,
        "errors": errors,
    }


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    (output_dir / "report.json").write_text(
        canonical_json(report) + "\n", encoding="utf-8", newline="\n"
    )
    lines = [
        "# Free E2E Report",
        "",
        f"Status: {report['status']}",
        f"Cases: {report['case_count']}",
        f"Approaches: {', '.join(report['approaches'])}",
        f"DB executions: {report['db_execution_count']}",
        f"Blocked non-executions: {report['blocked_execution_count']}",
        f"Paid API calls: {report['paid_api_calls']}",
        f"Errors: {len(report['errors'])}",
        "",
    ]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
