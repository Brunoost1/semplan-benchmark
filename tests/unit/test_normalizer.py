from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from semplan.catalog import load_catalog
from semplan.contracts import (
    Intent,
    Operation,
    OutOfScopeReasonCode,
    ResultOutcome,
    SemanticRequestEnvelope,
)
from semplan.errors import ProjectError
from semplan.normalizer import ReferenceContext, normalize_semantic_request
from semplan.sessions import StructuredSession


def _catalog():
    return load_catalog(Path("catalog"))


def _context() -> ReferenceContext:
    return ReferenceContext(date(2026, 8, 1), "UTC")


def _request(**overrides: object) -> SemanticRequestEnvelope:
    payload: dict[str, object] = {
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
        "confidence": Decimal("1"),
    }
    payload.update(overrides)
    return SemanticRequestEnvelope.model_validate(payload)


def test_normalizer_is_deterministic_for_same_inputs() -> None:
    request = _request()
    first = normalize_semantic_request(request, _catalog(), _context())
    second = normalize_semantic_request(request, _catalog(), _context())

    assert first == second
    assert first.plan is not None
    assert first.plan.plan_id == second.plan.plan_id


def test_normalizer_rejects_unknown_metric() -> None:
    request = _request(metrics=["imaginary_metric"])

    with pytest.raises(ProjectError):
        normalize_semantic_request(request, _catalog(), _context())


def test_normalizer_rejects_requested_incompatible_dimension() -> None:
    request = _request(metrics=["expense_amount"], dimensions=["channel"])

    with pytest.raises(ProjectError):
        normalize_semantic_request(request, _catalog(), _context())


def test_patch_preserves_unrelated_state_and_adds_filter() -> None:
    catalog = _catalog()
    session = StructuredSession(catalog, _context())
    initial = session.apply_request(_request())
    assert initial.plan is not None

    patch = _request(
        operation=Operation.PATCH,
        metrics=[],
        dimensions=[],
        filters=[{"field": "channel", "operator": "EQ", "value": "online"}],
        sort=[],
        limit=None,
    )
    patched = session.apply_request(patch)

    assert patched.plan is not None
    assert [metric.id for metric in patched.plan.metric_specs] == ["net_revenue"]
    assert [dimension.id for dimension in patched.plan.dimension_specs] == ["region"]
    fields = [leaf.field for leaf in patched.plan.predicate_tree.children]
    assert fields == ["year", "channel"]


def test_clarification_and_answer_are_deterministic_without_provider_rerun() -> None:
    catalog = _catalog()
    session = StructuredSession(catalog, _context())
    session.apply_request(_request())
    clarify = _request(
        operation=Operation.CLARIFY,
        intent=Intent.CLARIFICATION,
        metrics=[],
        dimensions=[],
        filters=[],
        sort=[],
        limit=None,
        clarifications=[
            {
                "reason_code": "AMBIGUOUS_METRIC",
                "question": "margin",
                "options": ["contribution_margin", "contribution_margin_pct"],
            }
        ],
    )

    result = session.apply_request(clarify)
    resolved = session.answer_clarification("contribution_margin")

    assert result.outcome is ResultOutcome.CLARIFY
    assert not session.pending_clarifications
    assert resolved.plan is not None
    assert [metric.id for metric in resolved.plan.metric_specs] == ["contribution_margin"]


def test_out_of_scope_preserves_session_state() -> None:
    catalog = _catalog()
    session = StructuredSession(catalog, _context())
    session.apply_request(_request())
    before = session.previous_plan
    request = _request(
        operation=Operation.OUT_OF_SCOPE,
        intent=Intent.OUT_OF_SCOPE,
        metrics=[],
        dimensions=[],
        filters=[],
        sort=[],
        limit=None,
        out_of_scope_reason=OutOfScopeReasonCode.WRITE_OPERATION,
    )

    result = session.apply_request(request)

    assert result.outcome is ResultOutcome.OUT_OF_SCOPE
    assert session.previous_plan == before
