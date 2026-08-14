"""Offline semantic-request fixtures for the public F3 smoke benchmark."""

from __future__ import annotations

import json
from pathlib import Path

from semplan.benchmark.validator import load_benchmark_cases
from semplan.contracts import (
    BenchmarkCase,
    ClarificationReasonCode,
    ExpectedPolicy,
    Intent,
    Operation,
    OutOfScopeReasonCode,
    PredicateGroup,
    PredicateLeaf,
    QuestionClass,
    SemanticPlanEnvelope,
)


def fixture_payloads_from_benchmark(benchmark_dir: Path) -> dict[str, dict[str, object]]:
    """Build deterministic fake-provider payloads from public approved artifacts."""

    payloads: dict[str, dict[str, object]] = {}
    for case in load_benchmark_cases(benchmark_dir):
        payloads[case.case_id] = fixture_payload_for_case(benchmark_dir, case)
    return payloads


def direct_sql_payloads_from_benchmark(benchmark_dir: Path) -> dict[str, dict[str, object]]:
    """Build deterministic A1 fake-provider payloads from public gold SQL."""

    payloads: dict[str, dict[str, object]] = {}
    for case in load_benchmark_cases(benchmark_dir):
        if case.expected_policy is ExpectedPolicy.ALLOW:
            if case.gold_sql_ref is None:
                raise ValueError(f"{case.case_id} missing gold SQL ref")
            payloads[case.case_id] = {
                "schema_version": "1.0",
                "sql": (benchmark_dir / case.gold_sql_ref).read_text(encoding="utf-8").strip(),
                "assumptions": [],
                "cannot_answer": False,
                "reason_code": None,
            }
        else:
            payloads[case.case_id] = {
                "schema_version": "1.0",
                "sql": None,
                "assumptions": [],
                "cannot_answer": True,
                "reason_code": _out_of_scope_reason_for_case(case).value,
            }
    return payloads


def tool_agent_payloads_from_benchmark(benchmark_dir: Path) -> dict[str, dict[str, object]]:
    """Build deterministic A2 fake-provider payloads from public gold plans."""

    payloads: dict[str, dict[str, object]] = {}
    for case in load_benchmark_cases(benchmark_dir):
        if case.expected_policy is ExpectedPolicy.ALLOW:
            semantic_request = fixture_payload_for_case(benchmark_dir, case)
            tool_name = "rank" if semantic_request["sort"] else "aggregate"
            payloads[case.case_id] = {
                "schema_version": "1.0",
                "tool_calls": [
                    {
                        "schema_version": "1.0",
                        "tool_name": tool_name,
                        "arguments": {
                            "metrics": semantic_request["metrics"],
                            "dimensions": semantic_request["dimensions"],
                            "filters": semantic_request["filters"],
                            "time_grain": semantic_request["time_grain"],
                            "sort": semantic_request["sort"]
                            if tool_name == "rank"
                            else semantic_request["sort"],
                            "limit": semantic_request["limit"],
                        },
                    }
                ],
                "final_request": semantic_request,
                "cannot_answer": False,
                "reason_code": None,
            }
        elif case.expected_policy is ExpectedPolicy.CLARIFY:
            payloads[case.case_id] = {
                "schema_version": "1.0",
                "tool_calls": [],
                "final_request": fixture_payload_for_case(benchmark_dir, case),
                "cannot_answer": False,
                "reason_code": None,
            }
        else:
            payloads[case.case_id] = {
                "schema_version": "1.0",
                "tool_calls": [],
                "final_request": None,
                "cannot_answer": True,
                "reason_code": OutOfScopeReasonCode.WRITE_OPERATION.value,
            }
    return payloads


def fixture_payload_for_case(benchmark_dir: Path, case: BenchmarkCase) -> dict[str, object]:
    if case.expected_policy is ExpectedPolicy.ALLOW:
        if case.gold_semantic_plan_ref is None:
            raise ValueError(f"{case.case_id} missing gold plan ref")
        plan = SemanticPlanEnvelope.model_validate_json(
            (benchmark_dir / case.gold_semantic_plan_ref).read_text(encoding="utf-8")
        )
        return {
            "schema_version": "1.0",
            "operation": plan.operation.value,
            "intent": _intent_for_case(case).value,
            "metrics": [metric.id for metric in plan.metric_specs],
            "dimensions": [dimension.id for dimension in plan.dimension_specs],
            "filters": [
                {
                    "field": leaf.field,
                    "operator": leaf.operator.value,
                    "value": leaf.value,
                }
                for leaf in _predicate_leaves(plan.predicate_tree)
            ],
            "time_grain": plan.time_context.grain.value if plan.time_context.grain else None,
            "sort": [
                {"field": sort.field, "direction": sort.direction.value} for sort in plan.sort_specs
            ],
            "limit": plan.limit,
            "comparison": None,
            "clarifications": [],
            "out_of_scope_reason": None,
            "confidence": "1",
        }
    if case.expected_policy is ExpectedPolicy.CLARIFY:
        options = case.clarification.acceptable_resolution_choices if case.clarification else []
        return {
            "schema_version": "1.0",
            "operation": Operation.CLARIFY.value,
            "intent": Intent.CLARIFICATION.value,
            "metrics": [],
            "dimensions": [],
            "filters": [],
            "time_grain": None,
            "sort": [],
            "limit": None,
            "comparison": None,
            "clarifications": [
                {
                    "reason_code": _clarification_reason(options).value,
                    "question": case.clarification.question_intent
                    if case.clarification
                    else "Clarify the request.",
                    "options": options,
                }
            ],
            "out_of_scope_reason": None,
            "confidence": "0.7",
        }
    return {
        "schema_version": "1.0",
        "operation": Operation.OUT_OF_SCOPE.value,
        "intent": Intent.OUT_OF_SCOPE.value,
        "metrics": [],
        "dimensions": [],
        "filters": [],
        "time_grain": None,
        "sort": [],
        "limit": None,
        "comparison": None,
        "clarifications": [],
        "out_of_scope_reason": OutOfScopeReasonCode.WRITE_OPERATION.value,
        "confidence": "1",
    }


def _intent_for_case(case: BenchmarkCase) -> Intent:
    if case.intent is QuestionClass.RANKING:
        return Intent.RANKING
    if case.intent is QuestionClass.COMPARISON:
        return Intent.COMPARISON
    if case.intent is QuestionClass.LOOKUP:
        return Intent.DETAIL_LOOKUP
    return Intent.GROUPED_METRIC


def _clarification_reason(options: list[str]) -> ClarificationReasonCode:
    if any(option.endswith("_revenue") or "margin" in option for option in options):
        return ClarificationReasonCode.AMBIGUOUS_METRIC
    return ClarificationReasonCode.AMBIGUOUS_SCOPE


def _out_of_scope_reason_for_case(case: BenchmarkCase) -> OutOfScopeReasonCode:
    if case.expected_policy is ExpectedPolicy.POLICY_VIOLATION:
        return OutOfScopeReasonCode.WRITE_OPERATION
    return OutOfScopeReasonCode.UNSUPPORTED_COMPUTATION


def _predicate_leaves(predicate: PredicateGroup | PredicateLeaf) -> list[PredicateLeaf]:
    if isinstance(predicate, PredicateLeaf):
        return [predicate]
    leaves: list[PredicateLeaf] = []
    for child in predicate.children:
        leaves.extend(_predicate_leaves(child))
    return leaves


def write_replay_fixture(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
