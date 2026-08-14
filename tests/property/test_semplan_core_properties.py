from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from semplan.catalog import load_catalog
from semplan.contracts import SemanticRequestEnvelope, ToleranceSpec
from semplan.evaluation import rows_equal
from semplan.normalizer import ReferenceContext, normalize_semantic_request
from semplan.sessions import StructuredSession


@settings(max_examples=20, derandomize=True)
@given(
    year=st.integers(min_value=2022, max_value=2026), limit=st.integers(min_value=1, max_value=10)
)
def test_normalizer_deterministic_for_generated_years(year: int, limit: int) -> None:
    catalog = load_catalog(Path("catalog"))
    request = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "REPLACE",
            "intent": "grouped_metric",
            "metrics": ["net_revenue"],
            "dimensions": ["region"],
            "filters": [{"field": "year", "operator": "EQ", "value": year}],
            "time_grain": "year",
            "sort": [{"field": "net_revenue", "direction": "desc"}],
            "limit": limit,
            "comparison": None,
            "clarifications": [],
            "confidence": "1",
        }
    )
    context = ReferenceContext(date(2026, 8, 1), "UTC")

    assert normalize_semantic_request(request, catalog, context) == normalize_semantic_request(
        request, catalog, context
    )


@settings(max_examples=12, derandomize=True)
@given(
    channel=st.sampled_from(["online", "marketplace", "retail", "wholesale", "partner", "mobile"])
)
def test_patch_locality_preserves_metric_and_dimension(channel: str) -> None:
    catalog = load_catalog(Path("catalog"))
    session = StructuredSession(catalog, ReferenceContext(date(2026, 8, 1), "UTC"))
    base = SemanticRequestEnvelope.model_validate(
        {
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
            "confidence": "1",
        }
    )
    session.apply_request(base)
    patch = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "PATCH",
            "intent": "grouped_metric",
            "metrics": [],
            "dimensions": [],
            "filters": [{"field": "channel", "operator": "EQ", "value": channel}],
            "time_grain": None,
            "sort": [],
            "limit": None,
            "comparison": None,
            "clarifications": [],
            "confidence": "1",
        }
    )

    result = session.apply_request(patch)

    assert result.plan is not None
    assert [metric.id for metric in result.plan.metric_specs] == ["net_revenue"]
    assert [dimension.id for dimension in result.plan.dimension_specs] == ["region"]


@settings(max_examples=20, derandomize=True)
@given(value=st.decimals(min_value=Decimal("0"), max_value=Decimal("1000"), places=2))
def test_canonical_comparison_is_symmetric_for_exact_values(value: Decimal) -> None:
    rows = [{"net_revenue": f"{value:.2f}"}]
    tolerances = {"net_revenue": ToleranceSpec(absolute=Decimal("0.01"), relative=Decimal("0"))}

    assert rows_equal(rows, list(rows), tolerances)
    assert rows_equal(list(rows), rows, tolerances)
