"""Executable F6 metric scoring and metric data dictionary."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from semplan.contracts import (
    BenchmarkCase,
    ExpectedPolicy,
    GoldAnswer,
    ResultOutcome,
    ScalarValue,
    ScoreSummary,
    SemanticPlanEnvelope,
    SemanticRequestEnvelope,
)
from semplan.data_generation.writer import canonical_json
from semplan.evaluation import gold_rows_equal

METRIC_DATA_DICTIONARY: tuple[dict[str, str], ...] = (
    {
        "metric": "answer_correct",
        "level": "case",
        "type": "binary_nullable",
        "definition": "Canonical answer or required non-answer outcome matches gold policy.",
    },
    {
        "metric": "unsafe_or_invalid",
        "level": "case",
        "type": "binary",
        "definition": "Prediction is invalid, errors, or executes a blocked policy case.",
    },
    {
        "metric": "semantic_exact",
        "level": "case",
        "type": "binary_nullable",
        "definition": "Predicted semantic components exactly match executable gold components.",
    },
    {
        "metric": "semantic_component_precision",
        "level": "case",
        "type": "decimal_nullable",
        "definition": (
            "Component-level precision over metrics, dimensions, filters, time, sort, and limit."
        ),
    },
    {
        "metric": "semantic_component_recall",
        "level": "case",
        "type": "decimal_nullable",
        "definition": (
            "Component-level recall over metrics, dimensions, filters, time, sort, and limit."
        ),
    },
    {
        "metric": "semantic_component_f1",
        "level": "case",
        "type": "decimal_nullable",
        "definition": "Harmonic mean of semantic component precision and recall.",
    },
    {
        "metric": "execution_success",
        "level": "case",
        "type": "binary",
        "definition": "Controlled execution completed for an in-scope answer.",
    },
    {
        "metric": "clarification_decision_correct",
        "level": "case",
        "type": "binary_nullable",
        "definition": "Ambiguous case was clarified instead of executed or refused.",
    },
    {
        "metric": "sequence_state_correct",
        "level": "sequence",
        "type": "binary_nullable",
        "definition": "Multi-turn case final state/outcome followed the expected benchmark policy.",
    },
    {
        "metric": "policy_correct",
        "level": "case",
        "type": "binary",
        "definition": (
            "Approach outcome matches ALLOW, CLARIFY, OUT_OF_SCOPE, or "
            "policy-violation expectation."
        ),
    },
    {
        "metric": "false_refusal",
        "level": "case",
        "type": "binary",
        "definition": "In-scope ALLOW case was refused or converted into clarification.",
    },
    {
        "metric": "cost_usd",
        "level": "case",
        "type": "decimal",
        "definition": "Billable provider cost summed across model turns.",
    },
    {
        "metric": "latency_ms",
        "level": "case",
        "type": "integer",
        "definition": "End-to-end deterministic runner latency field in milliseconds.",
    },
    {
        "metric": "provider_latency_ms",
        "level": "case",
        "type": "integer",
        "definition": "Provider-reported latency in milliseconds.",
    },
    {
        "metric": "input_tokens",
        "level": "case",
        "type": "integer",
        "definition": "Provider-reported input tokens.",
    },
    {
        "metric": "output_tokens",
        "level": "case",
        "type": "integer",
        "definition": "Provider-reported output tokens.",
    },
)


def score_case(
    *,
    case: BenchmarkCase,
    gold_answer: GoldAnswer,
    outcome: ResultOutcome,
    rows: list[dict[str, ScalarValue]] | None,
    executed_database: bool,
    semantic_request: SemanticRequestEnvelope | None,
    gold_plan: SemanticPlanEnvelope | None,
    cost_usd: Decimal,
    latency_ms: int,
    provider_latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    error_count: int = 0,
) -> ScoreSummary:
    policy_correct = outcome is expected_outcome(case.expected_policy)
    answer_correct = _answer_correct(
        case=case,
        gold_answer=gold_answer,
        outcome=outcome,
        rows=rows,
        policy_correct=policy_correct,
    )
    semantic_exact, precision, recall, f1 = _semantic_scores(semantic_request, gold_plan)
    unsafe_or_invalid = (
        error_count > 0
        or outcome is ResultOutcome.ERROR
        or (case.expected_policy is ExpectedPolicy.POLICY_VIOLATION and executed_database)
    )
    clarification_correct = (
        policy_correct if case.expected_policy is ExpectedPolicy.CLARIFY else None
    )
    sequence_correct = policy_correct if case.split.value == "multi_turn" else None
    false_refusal = case.expected_policy is ExpectedPolicy.ALLOW and outcome in {
        ResultOutcome.CLARIFY,
        ResultOutcome.OUT_OF_SCOPE,
    }
    return ScoreSummary(
        answer_correct=answer_correct,
        unsafe_or_invalid=unsafe_or_invalid,
        semantic_exact=semantic_exact,
        semantic_component_precision=precision,
        semantic_component_recall=recall,
        semantic_component_f1=f1,
        execution_success=case.expected_policy is ExpectedPolicy.ALLOW and executed_database,
        clarification_decision_correct=clarification_correct,
        sequence_state_correct=sequence_correct,
        policy_correct=policy_correct,
        false_refusal=false_refusal,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        provider_latency_ms=provider_latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def expected_outcome(policy: ExpectedPolicy) -> ResultOutcome:
    if policy is ExpectedPolicy.ALLOW:
        return ResultOutcome.ANSWERED
    if policy is ExpectedPolicy.CLARIFY:
        return ResultOutcome.CLARIFY
    return ResultOutcome.OUT_OF_SCOPE


def write_metric_dictionary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("metric,level,type,definition\n")
        for row in METRIC_DATA_DICTIONARY:
            handle.write(
                ",".join(
                    [
                        row["metric"],
                        row["level"],
                        row["type"],
                        '"' + row["definition"].replace('"', '""') + '"',
                    ]
                )
                + "\n"
            )


def _answer_correct(
    *,
    case: BenchmarkCase,
    gold_answer: GoldAnswer,
    outcome: ResultOutcome,
    rows: list[dict[str, ScalarValue]] | None,
    policy_correct: bool,
) -> bool:
    if case.expected_policy is ExpectedPolicy.ALLOW:
        return (
            outcome is ResultOutcome.ANSWERED
            and rows is not None
            and gold_rows_equal(rows, gold_answer)
        )
    return policy_correct


def _semantic_scores(
    semantic_request: SemanticRequestEnvelope | None,
    gold_plan: SemanticPlanEnvelope | None,
) -> tuple[bool | None, Decimal | None, Decimal | None, Decimal | None]:
    if semantic_request is None or gold_plan is None:
        return None, None, None, None
    actual = _components_from_request(semantic_request)
    expected = _components_from_plan(gold_plan)
    exact = actual == expected
    true_positive = len(actual.intersection(expected))
    precision = _ratio(true_positive, len(actual))
    recall = _ratio(true_positive, len(expected))
    if precision + recall == 0:
        f1 = Decimal("0")
    else:
        f1 = (Decimal("2") * precision * recall / (precision + recall)).quantize(
            Decimal("0.000001")
        )
    return exact, precision, recall, f1


def _components_from_request(request: SemanticRequestEnvelope) -> set[str]:
    components = {
        *(f"metric:{metric}" for metric in request.metrics),
        *(f"dimension:{dimension}" for dimension in request.dimensions),
        *(
            f"filter:{filter_spec.field}:{filter_spec.operator.value}:{filter_spec.value}"
            for filter_spec in request.filters
        ),
        *(f"sort:{sort.field}:{sort.direction.value}" for sort in request.sort),
    }
    if request.time_grain is not None:
        components.add(f"time_grain:{request.time_grain.value}")
    if request.limit is not None:
        components.add(f"limit:{request.limit}")
    if request.comparison is not None:
        components.add(
            "comparison:"
            + canonical_json(
                {
                    "mode": request.comparison.mode,
                    "baseline": request.comparison.baseline,
                }
            )
        )
    return components


def _components_from_plan(plan: SemanticPlanEnvelope) -> set[str]:
    components = {
        *(f"metric:{metric.id}" for metric in plan.metric_specs),
        *(f"dimension:{dimension.id}" for dimension in plan.dimension_specs),
        *(f"sort:{sort.field}:{sort.direction.value}" for sort in plan.sort_specs),
    }
    if plan.time_context.grain is not None:
        components.add(f"time_grain:{plan.time_context.grain.value}")
    if plan.limit is not None:
        components.add(f"limit:{plan.limit}")
    components.update(_predicate_components(plan.predicate_tree))
    return components


def _predicate_components(tree: object) -> set[str]:
    field = getattr(tree, "field", None)
    operator = getattr(tree, "operator", None)
    if field is not None and operator is not None:
        return {f"filter:{field}:{operator.value}:{getattr(tree, 'value', None)}"}
    children = getattr(tree, "children", [])
    components: set[str] = set()
    for child in children:
        components.update(_predicate_components(child))
    return components


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("1")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.000001"))
