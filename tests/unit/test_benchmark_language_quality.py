from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from semplan.benchmark.language_quality import audit_cases_language_quality
from semplan.benchmark.localization import (
    DIMENSION_SURFACE,
    METRIC_SURFACE,
    dimension_surface,
    metric_noun_phrase_pt,
    metric_surface,
    value_surface,
)
from semplan.benchmark.release_scale import _build_case, build_release_specs
from semplan.catalog import load_catalog
from semplan.contracts import (
    BenchmarkCase,
    BenchmarkContext,
    BenchmarkReview,
    DatasetSplit,
    Difficulty,
    ExpectedPolicy,
    Locale,
    Operation,
    QuestionClass,
    ReviewStatus,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_pt_br_language_quality_detects_unlocalized_surface_terms() -> None:
    case = _case(
        "Compare net revenue por quarter para region North, channel online.",
        language=Locale.PT_BR,
    )

    report = audit_cases_language_quality([case])

    assert report["status"] == "failed"
    assert report["affected_pt_br_case_count"] == 1
    assert report["affected_terms"]["net revenue"] == 1
    assert report["affected_terms"]["quarter"] == 1
    assert report["affected_terms"]["region"] == 1
    assert report["affected_terms"]["North"] == 1


def test_surface_forms_cover_catalog_metrics_and_dimensions() -> None:
    catalog = load_catalog(PROJECT_ROOT / "catalog")

    missing_metrics = sorted(set(catalog.metrics) - set(METRIC_SURFACE))
    missing_dimensions = sorted(set(catalog.dimensions) - set(DIMENSION_SURFACE))

    assert missing_metrics == []
    assert missing_dimensions == []
    for metric_id in catalog.metrics:
        assert metric_surface(metric_id, Locale.EN_US)
        assert metric_surface(metric_id, Locale.PT_BR)
        assert "_" not in metric_surface(metric_id, Locale.PT_BR)
        assert metric_id not in metric_noun_phrase_pt(metric_id)
    for dimension_id in catalog.dimensions:
        assert dimension_surface(dimension_id, Locale.EN_US)
        assert dimension_surface(dimension_id, Locale.PT_BR)
        assert "_" not in dimension_surface(dimension_id, Locale.PT_BR)


@pytest.mark.parametrize(
    ("field", "raw", "expected_pt"),
    [
        ("region", "North", "Norte"),
        ("region", "South", "Sul"),
        ("region", "East", "Leste"),
        ("region", "West", "Oeste"),
        ("customer_segment", "consumer", "consumidor"),
        ("customer_segment", "small_business", "pequenas empresas"),
        ("customer_segment", "mid_market", "mercado intermediário"),
        ("customer_segment", "enterprise", "empresarial"),
        ("payment_method", "card", "cartão"),
        ("payment_method", "invoice", "fatura"),
        ("payment_method", "bank_transfer", "transferência bancária"),
        ("payment_method", "wallet", "carteira digital"),
        ("payment_method", "voucher", "voucher"),
        ("department", "department_03", "departamento 03"),
        ("expense_category", "expense_04", "categoria de despesa 04"),
        ("category", "category_01", "categoria 01"),
    ],
)
def test_representative_enum_and_opaque_value_localization(
    field: str, raw: str, expected_pt: str
) -> None:
    assert value_surface(field, raw, Locale.PT_BR) == expected_pt


def test_release_scale_templates_have_clean_pt_br_surface_and_all_taxonomies() -> None:
    cases = []
    for index, spec in enumerate(build_release_specs(), start=1):
        cases.append(_build_case(spec, Locale.EN_US, f"DEV-REL-{index * 2 - 1:06d}"))
        cases.append(_build_case(spec, Locale.PT_BR, f"DEV-REL-{index * 2:06d}"))

    report = audit_cases_language_quality(cases)
    class_counts = Counter(case.intent for case in cases if case.language is Locale.PT_BR)

    assert report["status"] == "passed"
    assert report["affected_pt_br_case_count"] == 0
    assert set(class_counts) == set(QuestionClass)
    assert class_counts[QuestionClass.ADVERSARIAL] == 100
    assert class_counts[QuestionClass.MULTI_TURN] == 100


def _case(utterance: str, *, language: Locale) -> BenchmarkCase:
    return BenchmarkCase(
        schema_version="1.0",
        case_id="DEV-REL-000001",
        split=DatasetSplit.DEVELOPMENT,
        language=language,
        utterance=utterance,
        context=BenchmarkContext(reference_date=date(2026, 8, 1), timezone="UTC"),
        expected_operation=Operation.REPLACE,
        intent=QuestionClass.LOOKUP,
        difficulty=Difficulty.EASY,
        requires_clarification=False,
        gold_semantic_plan_ref="gold/plans/DEV-REL-000001.json",
        gold_sql_ref="gold/sql/DEV-REL-000001.sql",
        gold_answer_ref="gold/answers/DEV-REL-000001.json",
        expected_policy=ExpectedPolicy.ALLOW,
        tags=["lookup"],
        template_family="language_quality_fixture",
        semantic_fingerprint="sha256:" + ("0" * 64),
        review=BenchmarkReview(status=ReviewStatus.PENDING_AUTHOR_REVIEW),
    )
