from __future__ import annotations

import pytest
from pydantic import ValidationError

from semplan.contracts import FilterSpec, SemanticRequestEnvelope, ToolCallEnvelope


def test_filter_spec_rejects_null_operator_with_value() -> None:
    with pytest.raises(ValidationError):
        FilterSpec.model_validate({"field": "region", "operator": "IS_NULL", "value": "North"})


def test_filter_spec_rejects_bad_between_values() -> None:
    with pytest.raises(ValidationError):
        FilterSpec.model_validate({"field": "date", "operator": "BETWEEN", "value": ["2026"]})


def test_filter_spec_rejects_empty_in_values() -> None:
    with pytest.raises(ValidationError):
        FilterSpec.model_validate({"field": "region", "operator": "IN", "value": []})


def test_filter_spec_rejects_list_for_scalar_operator() -> None:
    with pytest.raises(ValidationError):
        FilterSpec.model_validate({"field": "region", "operator": "EQ", "value": ["North"]})


def test_semantic_request_clarify_requires_clarification() -> None:
    with pytest.raises(ValidationError):
        SemanticRequestEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "operation": "CLARIFY",
                "intent": "clarification",
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "sort": [],
                "clarifications": [],
                "confidence": "0.5",
            }
        )


def test_semantic_request_out_of_scope_requires_matching_intent() -> None:
    with pytest.raises(ValidationError):
        SemanticRequestEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "operation": "OUT_OF_SCOPE",
                "intent": "grouped_metric",
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "sort": [],
                "clarifications": [],
                "out_of_scope_reason": "WRITE_OPERATION",
                "confidence": "0.5",
            }
        )


def test_semantic_request_out_of_scope_requires_reason() -> None:
    with pytest.raises(ValidationError):
        SemanticRequestEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "operation": "OUT_OF_SCOPE",
                "intent": "out_of_scope",
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "sort": [],
                "clarifications": [],
                "confidence": "0.5",
            }
        )


def test_semantic_request_replace_requires_metric() -> None:
    with pytest.raises(ValidationError):
        SemanticRequestEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "operation": "REPLACE",
                "intent": "grouped_metric",
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "sort": [],
                "clarifications": [],
                "confidence": "0.5",
            }
        )


def test_semantic_request_patch_allows_filter_only_change() -> None:
    request = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "PATCH",
            "intent": "grouped_metric",
            "metrics": [],
            "dimensions": [],
            "filters": [{"field": "channel", "operator": "EQ", "value": "online"}],
            "sort": [],
            "clarifications": [],
            "confidence": "0.5",
        }
    )

    assert request.metrics == []
    assert request.filters[0].field == "channel"


def test_semantic_request_clarification_has_typed_reason() -> None:
    request = SemanticRequestEnvelope.model_validate(
        {
            "schema_version": "1.0",
            "operation": "CLARIFY",
            "intent": "clarification",
            "metrics": [],
            "dimensions": [],
            "filters": [],
            "sort": [],
            "clarifications": [
                {
                    "reason_code": "AMBIGUOUS_METRIC",
                    "question": "margin",
                    "options": ["contribution_margin", "contribution_margin_pct"],
                }
            ],
            "confidence": "0.5",
        }
    )

    assert request.clarifications[0].reason_code == "AMBIGUOUS_METRIC"


def test_tool_call_rejects_invalid_argument_names() -> None:
    with pytest.raises(ValidationError):
        ToolCallEnvelope.model_validate(
            {
                "schema_version": "1.0",
                "tool_name": "lookup_metric",
                "arguments": {"bad-name": "net_revenue"},
            }
        )
