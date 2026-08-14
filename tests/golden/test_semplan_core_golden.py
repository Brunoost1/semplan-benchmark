from __future__ import annotations

from datetime import date
from pathlib import Path

from semplan.approaches.semantic_plan import fixture_payload_for_case
from semplan.benchmark.validator import load_benchmark_cases
from semplan.catalog import load_catalog
from semplan.contracts import ResultOutcome, SemanticPlanEnvelope, SemanticRequestEnvelope
from semplan.normalizer import ReferenceContext, normalize_semantic_request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = PROJECT_ROOT / "data/benchmark/f3_smoke"


def _case(case_id: str):
    return next(case for case in load_benchmark_cases(BENCHMARK_DIR) if case.case_id == case_id)


def test_allow_case_normalizes_to_gold_semantic_fields() -> None:
    catalog = load_catalog(PROJECT_ROOT / "catalog")
    case = _case("DEV-SMK-000003")
    request = SemanticRequestEnvelope.model_validate(fixture_payload_for_case(BENCHMARK_DIR, case))
    result = normalize_semantic_request(
        request,
        catalog,
        ReferenceContext(date(2026, 8, 1), "UTC"),
    )
    assert case.gold_semantic_plan_ref is not None
    gold = SemanticPlanEnvelope.model_validate_json(
        (BENCHMARK_DIR / case.gold_semantic_plan_ref).read_text(encoding="utf-8")
    )

    assert result.outcome is ResultOutcome.ANSWERED
    assert result.plan is not None
    assert [metric.id for metric in result.plan.metric_specs] == [
        metric.id for metric in gold.metric_specs
    ]
    assert [dimension.id for dimension in result.plan.dimension_specs] == [
        dimension.id for dimension in gold.dimension_specs
    ]
    assert result.plan.sort_specs == gold.sort_specs


def test_clarification_case_renders_bilingual_options() -> None:
    catalog = load_catalog(PROJECT_ROOT / "catalog")
    case = _case("MT-SMK-000003")
    request = SemanticRequestEnvelope.model_validate(fixture_payload_for_case(BENCHMARK_DIR, case))
    result = normalize_semantic_request(
        request,
        catalog,
        ReferenceContext(date(2026, 8, 1), "UTC"),
    )

    assert result.outcome is ResultOutcome.CLARIFY
    assert result.clarification is not None
    assert [option.option_id for option in result.clarification.options] == [
        "contribution_margin",
        "contribution_margin_pct",
    ]
    assert result.clarification.question.en_us == "Which metric should be used?"


def test_adversarial_case_produces_policy_response_without_plan() -> None:
    catalog = load_catalog(PROJECT_ROOT / "catalog")
    case = _case("ADV-SMK-000001")
    request = SemanticRequestEnvelope.model_validate(fixture_payload_for_case(BENCHMARK_DIR, case))
    result = normalize_semantic_request(
        request,
        catalog,
        ReferenceContext(date(2026, 8, 1), "UTC"),
    )

    assert result.outcome is ResultOutcome.OUT_OF_SCOPE
    assert result.plan is None
    assert result.out_of_scope is not None
    assert result.out_of_scope.reason_code == "WRITE_OPERATION"
