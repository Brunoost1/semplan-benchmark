from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from semplan.approaches.semantic_plan import fixture_payloads_from_benchmark
from semplan.benchmark.validator import load_benchmark_cases
from semplan.contracts import (
    ExpectedPolicy,
    GoldAnswer,
    ResultOutcome,
    SemanticPlanEnvelope,
    SemanticRequestEnvelope,
)
from semplan.experiments.scoring import METRIC_DATA_DICTIONARY, score_case

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


def test_score_allow_case_correct_rows() -> None:
    case = next(
        case
        for case in load_benchmark_cases(BENCHMARK_DIR)
        if case.expected_policy is ExpectedPolicy.ALLOW
    )
    gold = GoldAnswer.model_validate_json(
        (BENCHMARK_DIR / case.gold_answer_ref).read_text(encoding="utf-8")
    )

    scores = score_case(
        case=case,
        gold_answer=gold,
        outcome=ResultOutcome.ANSWERED,
        rows=gold.rows,
        executed_database=True,
        semantic_request=None,
        gold_plan=None,
        cost_usd=Decimal("0"),
        latency_ms=0,
        provider_latency_ms=0,
        input_tokens=1,
        output_tokens=1,
    )

    assert scores.answer_correct is True
    assert scores.execution_success is True
    assert scores.policy_correct is True
    assert scores.false_refusal is False


def test_score_clarification_case_false_refusal_for_wrong_outcome() -> None:
    case = next(
        case
        for case in load_benchmark_cases(BENCHMARK_DIR)
        if case.expected_policy is ExpectedPolicy.CLARIFY
    )
    gold = GoldAnswer.model_validate_json(
        (BENCHMARK_DIR / case.gold_answer_ref).read_text(encoding="utf-8")
    )

    scores = score_case(
        case=case,
        gold_answer=gold,
        outcome=ResultOutcome.OUT_OF_SCOPE,
        rows=None,
        executed_database=False,
        semantic_request=None,
        gold_plan=None,
        cost_usd=Decimal("0"),
        latency_ms=0,
        provider_latency_ms=0,
        input_tokens=1,
        output_tokens=1,
    )

    assert scores.answer_correct is False
    assert scores.clarification_decision_correct is False
    assert scores.policy_correct is False


def test_score_execution_failed_as_invalid_model_outcome() -> None:
    case = next(
        case
        for case in load_benchmark_cases(BENCHMARK_DIR)
        if case.expected_policy is ExpectedPolicy.ALLOW
    )
    gold = GoldAnswer.model_validate_json(
        (BENCHMARK_DIR / case.gold_answer_ref).read_text(encoding="utf-8")
    )

    scores = score_case(
        case=case,
        gold_answer=gold,
        outcome=ResultOutcome.ERROR,
        rows=None,
        executed_database=False,
        semantic_request=None,
        gold_plan=None,
        cost_usd=Decimal("0.0001"),
        latency_ms=0,
        provider_latency_ms=0,
        input_tokens=10,
        output_tokens=5,
        error_count=1,
    )

    assert scores.answer_correct is False
    assert scores.execution_success is False
    assert scores.unsafe_or_invalid is True
    assert scores.policy_correct is False


def test_metric_dictionary_contains_primary_metrics() -> None:
    metric_names = {row["metric"] for row in METRIC_DATA_DICTIONARY}

    assert {
        "answer_correct",
        "unsafe_or_invalid",
        "semantic_exact",
        "policy_correct",
        "cost_usd",
        "latency_ms",
    }.issubset(metric_names)


def test_score_semantic_components_and_metric_dictionary_file(tmp_path: Path) -> None:
    case = next(
        case
        for case in load_benchmark_cases(BENCHMARK_DIR)
        if case.expected_policy is ExpectedPolicy.ALLOW
    )
    gold = GoldAnswer.model_validate_json(
        (BENCHMARK_DIR / case.gold_answer_ref).read_text(encoding="utf-8")
    )
    assert case.gold_semantic_plan_ref is not None
    gold_plan = SemanticPlanEnvelope.model_validate_json(
        (BENCHMARK_DIR / case.gold_semantic_plan_ref).read_text(encoding="utf-8")
    )
    semantic_request = SemanticRequestEnvelope.model_validate(
        fixture_payloads_from_benchmark(BENCHMARK_DIR)[case.case_id]
    )

    scores = score_case(
        case=case,
        gold_answer=gold,
        outcome=ResultOutcome.ANSWERED,
        rows=gold.rows,
        executed_database=True,
        semantic_request=semantic_request,
        gold_plan=gold_plan,
        cost_usd=Decimal("0"),
        latency_ms=0,
        provider_latency_ms=0,
        input_tokens=1,
        output_tokens=1,
    )

    assert scores.semantic_exact is True
    assert scores.semantic_component_f1 == Decimal("1.000000")

    from semplan.experiments.scoring import write_metric_dictionary

    dictionary_path = tmp_path / "metrics.csv"
    write_metric_dictionary(dictionary_path)
    assert "answer_correct" in dictionary_path.read_text(encoding="utf-8")
