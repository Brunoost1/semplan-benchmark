from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from semplan.contracts import (
    BenchmarkCase,
    BenchmarkManifest,
    BudgetCheck,
    CanonicalResponse,
    DirectSqlEnvelope,
    GoldAnswer,
    PriceTable,
    PromptMetadata,
    ProviderRequest,
    ProviderResponse,
    ResultRecord,
    RunManifest,
    SemanticPlanEnvelope,
    SemanticRequestEnvelope,
    ToolAgentTurnEnvelope,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "schemas"

EXAMPLES: dict[str, tuple[type[BaseModel], dict[str, Any]]] = {
    "semantic_request.schema.json": (
        SemanticRequestEnvelope,
        {
            "schema_version": "1.0",
            "operation": "REPLACE",
            "intent": "grouped_metric",
            "metrics": ["net_revenue"],
            "dimensions": ["region"],
            "filters": [
                {
                    "field": "order_date",
                    "operator": "BETWEEN",
                    "value": ["2026-04-01", "2026-06-30"],
                }
            ],
            "time_grain": "quarter",
            "sort": [{"field": "net_revenue", "direction": "desc"}],
            "limit": 10,
            "comparison": None,
            "clarifications": [],
            "out_of_scope_reason": None,
            "confidence": "0.91",
        },
    ),
    "semantic_plan.schema.json": (
        SemanticPlanEnvelope,
        {
            "schema_version": "1.0",
            "plan_id": "uuid5:semantic-plan-fixture",
            "operation": "REPLACE",
            "metric_specs": [{"id": "net_revenue", "aggregation": "SUM"}],
            "dimension_specs": [{"id": "region"}],
            "predicate_tree": {"type": "AND", "children": []},
            "time_context": {
                "reference_date": "2026-08-01",
                "timezone": "UTC",
                "grain": "quarter",
            },
            "sort_specs": [{"field": "net_revenue", "direction": "desc"}],
            "limit": 10,
            "execution": {"operator": "aggregate", "policy": "read_only", "max_rows": 1000},
            "provenance": {
                "request_hash": "sha256:" + ("a" * 64),
                "normalizer_version": "0.1.0",
                "catalog_hash": "sha256:" + ("b" * 64),
                "defaults": [],
            },
            "status": "READY",
        },
    ),
    "benchmark_case.schema.json": (
        BenchmarkCase,
        {
            "schema_version": "1.0",
            "case_id": "DEV-SMK-000001",
            "split": "development",
            "language": "pt-BR",
            "utterance": "Mostre receita liquida por regiao.",
            "context": {"reference_date": "2026-08-01", "timezone": "UTC"},
            "expected_operation": "REPLACE",
            "intent": "grouped_aggregation",
            "difficulty": "easy",
            "requires_clarification": False,
            "gold_semantic_plan_ref": "gold/plans/DEV-SMK-000001.json",
            "gold_sql_ref": "gold/sql/DEV-SMK-000001.sql",
            "gold_answer_ref": "gold/answers/DEV-SMK-000001.json",
            "expected_policy": "ALLOW",
            "tags": ["aggregation", "revenue"],
            "template_family": "net_revenue_by_region",
            "semantic_fingerprint": "sha256:" + ("c" * 64),
            "clarification": None,
            "review": {
                "status": "pending_author_review",
                "reviewer": None,
                "reviewed_at": None,
                "notes": [],
            },
        },
    ),
    "benchmark_manifest.schema.json": (
        BenchmarkManifest,
        {
            "schema_version": "1.0",
            "benchmark_version": "0.1.0",
            "dataset_version": "0.1.0",
            "dataset_manifest_hash": "sha256:" + ("d" * 64),
            "state": "validated",
            "case_count": 50,
            "split_counts": {"development": 30, "validation": 10, "test_public": 10},
            "language_counts": {"en-US": 25, "pt-BR": 25},
            "file_hashes": {"cases/development.jsonl": "e" * 64},
            "hidden_included": False,
            "review_summary": {"pending_author_review": 100},
        },
    ),
    "gold_answer.schema.json": (
        GoldAnswer,
        {
            "schema_version": "1.0",
            "case_id": "DEV-SMK-000001",
            "outcome": "ALLOW",
            "dataset_version": "0.1.0",
            "dataset_manifest_hash": "sha256:" + ("f" * 64),
            "query_hash": "sha256:" + ("1" * 64),
            "plan_hash": "sha256:" + ("2" * 64),
            "execution_timestamp_utc": "2026-08-06T00:00:00Z",
            "rows": [{"region": "North", "net_revenue": "123.45"}],
            "units": {"net_revenue": "usd"},
            "ordering": {
                "ordered": True,
                "fields": ["net_revenue", "region"],
                "tie_policy": "deterministic secondary sort",
            },
            "tolerances": {"net_revenue": {"absolute": "0.01", "relative": "0"}},
            "assumptions": [],
            "review": {
                "status": "pending_author_review",
                "reviewer": None,
                "reviewed_at": None,
                "notes": [],
            },
        },
    ),
    "run_manifest.schema.json": (
        RunManifest,
        {
            "schema_version": "1.0",
            "run_id": "manifest-001",
            "status": "frozen",
            "created_at_utc": "2026-08-06T00:00:00Z",
            "code_commit": "a" * 40,
            "dirty_tree": False,
            "non_reportable": False,
            "dataset_version": "0.1.0",
            "dataset_manifest_sha256": "sha256:" + ("b" * 64),
            "benchmark_manifest_sha256": "sha256:" + ("c" * 64),
            "catalog_sha256": "sha256:" + ("d" * 64),
            "approaches": ["A1", "A2", "A3", "A4"],
            "model": {
                "provider": "openai",
                "id": "gpt-5.6-luna",
                "reasoning_effort": "low",
                "parameters": {"temperature": "0", "max_output_tokens": 1200},
            },
            "prompts": {
                "A1": {
                    "id": "direct_sql_a1_v1",
                    "sha256": "sha256:" + ("1" * 64),
                    "output_schema_ref": "direct_sql.schema.json",
                    "output_schema_sha256": "sha256:" + ("2" * 64),
                },
                "A2": {
                    "id": "tool_agent_a2_v1",
                    "sha256": "sha256:" + ("3" * 64),
                    "output_schema_ref": "tool_agent_turn.schema.json",
                    "output_schema_sha256": "sha256:" + ("4" * 64),
                },
                "A3": {
                    "id": "semantic_request_a3_v1",
                    "sha256": "sha256:" + ("5" * 64),
                    "output_schema_ref": "semantic_request.schema.json",
                    "output_schema_sha256": "sha256:" + ("6" * 64),
                },
                "A4": {
                    "id": "semantic_request_a4_v1",
                    "sha256": "sha256:" + ("7" * 64),
                    "output_schema_ref": "semantic_request.schema.json",
                    "output_schema_sha256": "sha256:" + ("8" * 64),
                },
            },
            "splits": ["development", "validation", "test_public"],
            "repetitions": 3,
            "randomization_seed": 20260806,
            "budget_usd": "18.00",
            "price_table_sha256": "sha256:" + ("e" * 64),
            "execution_policy_sha256": "sha256:" + ("f" * 64),
            "mode": "synchronous",
            "allow_paid": False,
        },
    ),
    "direct_sql.schema.json": (
        DirectSqlEnvelope,
        {
            "schema_version": "1.0",
            "sql": (
                "SELECT region, sum(net_revenue) AS net_revenue "
                "FROM analytics_order_facts GROUP BY region"
            ),
            "assumptions": [],
            "cannot_answer": False,
            "reason_code": None,
        },
    ),
    "tool_agent_turn.schema.json": (
        ToolAgentTurnEnvelope,
        {
            "schema_version": "1.0",
            "tool_calls": [
                {
                    "schema_version": "1.0",
                    "tool_name": "aggregate",
                    "arguments": {
                        "metrics": ["net_revenue"],
                        "dimensions": ["region"],
                        "filters": [{"field": "year", "operator": "EQ", "value": 2026}],
                        "time_grain": "year",
                        "sort": [{"field": "net_revenue", "direction": "desc"}],
                        "limit": 5,
                    },
                }
            ],
            "final_request": None,
            "cannot_answer": False,
            "reason_code": None,
        },
    ),
    "price_table.schema.json": (
        PriceTable,
        {
            "schema_version": "1.0",
            "provider": "openai",
            "source": "Owner-verified official OpenAI pricing snapshot.",
            "checked_at_utc": "2026-08-06T00:00:00Z",
            "currency": "USD",
            "model_prices": {
                "gpt-5.6-luna": {
                    "input_per_million_usd": "0.10",
                    "output_per_million_usd": "0.40",
                    "cached_input_per_million_usd": "0.02",
                    "batch_input_per_million_usd": None,
                    "batch_output_per_million_usd": None,
                }
            },
        },
    ),
    "budget_check.schema.json": (
        BudgetCheck,
        {
            "schema_version": "1.0",
            "request_hash": "sha256:" + ("9" * 64),
            "model": "gpt-5.6-luna",
            "estimated_input_tokens": 100,
            "estimated_output_tokens": 500,
            "safety_multiplier": "1.20",
            "estimated_usd": "0.000240",
            "run_budget_usd": "3.00",
            "monthly_limit_usd": "20.00",
            "remaining_run_budget_usd": "3.00",
            "remaining_monthly_budget_usd": "20.00",
            "price_checked_at_utc": "2026-08-06T00:00:00Z",
        },
    ),
    "provider_request.schema.json": (
        ProviderRequest,
        {
            "schema_version": "1.0",
            "provider": "fake",
            "model": "fake-semantic-request-v1",
            "prompt_id": "semantic_request_a3_v1",
            "prompt_sha256": "sha256:" + ("3" * 64),
            "system": "Use strict semantic requests.",
            "inputs": ["Show 2026 net revenue by region."],
            "output_schema_ref": "semantic_request.schema.json",
            "output_schema_sha256": "sha256:" + ("8" * 64),
            "inference_parameters": {"temperature": "0"},
            "timeout_seconds": 30,
            "metadata": {"case_id": "DEV-SMK-000001"},
            "idempotency_hash": "sha256:" + ("4" * 64),
        },
    ),
    "provider_response.schema.json": (
        ProviderResponse,
        {
            "schema_version": "1.0",
            "provider": "fake",
            "model": "fake-semantic-request-v1",
            "response_id": "fake-001",
            "finish_status": "STOP",
            "raw_payload": {"id": "fake-001"},
            "parsed_payload": {
                "schema_version": "1.0",
                "operation": "REPLACE",
                "intent": "grouped_metric",
                "metrics": ["net_revenue"],
                "dimensions": ["region"],
                "filters": [],
                "time_grain": "year",
                "sort": [{"field": "net_revenue", "direction": "desc"}],
                "limit": 5,
                "comparison": None,
                "clarifications": [],
                "out_of_scope_reason": None,
                "confidence": "1",
            },
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "cost": {"estimated_usd": "0", "currency": "USD"},
            "timing_ms": 0,
            "attempts": 1,
            "refusal": None,
        },
    ),
    "prompt_metadata.schema.json": (
        PromptMetadata,
        {
            "schema_version": "1.0",
            "prompt_id": "semantic_request_a3_v1",
            "version": "1.0.0",
            "approach": "A3",
            "locale_strategy": "case_locale_only",
            "expected_output_schema": "semantic_request.schema.json",
            "author": "SemPlan Benchmark",
            "reviewer": "Bruno Santos Teixeira",
            "changelog": ["Initial prompt."],
            "template_file": "prompt.md",
        },
    ),
    "canonical_response.schema.json": (
        CanonicalResponse,
        {
            "schema_version": "1.0",
            "outcome": "ANSWERED",
            "rows": [{"region": "North", "net_revenue": "123.45"}],
            "units": {"net_revenue": "usd"},
            "assumptions": [],
            "message": {
                "en-US": "Returned 1 canonical row(s).",
                "pt-BR": "Retornou 1 linha(s) canonica(s).",
            },
            "clarification": None,
            "out_of_scope": None,
        },
    ),
    "result_record.schema.json": (
        ResultRecord,
        {
            "schema_version": "1.0",
            "run_id": "run-001",
            "work_item_id": "sha256:" + ("a" * 64),
            "case_id": "case-001",
            "approach": "A3",
            "repetition": 1,
            "input": {
                "utterance_sha256": "sha256:" + ("b" * 64),
                "state_sha256": None,
                "split": "development",
                "language": "en-US",
            },
            "provider": {
                "request_sha256": "sha256:" + ("c" * 64),
                "request_ref": {
                    "path": "rendered_prompts/a.json",
                    "sha256": "sha256:" + ("d" * 64),
                },
                "response_ref": {
                    "path": "raw/a.json",
                    "sha256": "sha256:" + ("e" * 64),
                },
                "model_requested": "gpt-5.6-luna",
                "model_returned": "gpt-5.6-luna",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "cost_usd": "0.000001",
            },
            "prediction": {
                "status": "parsed",
                "artifact_ref": {
                    "path": "predictions/a.json",
                    "sha256": "sha256:" + ("f" * 64),
                },
            },
            "execution": {
                "policy": "ALLOW",
                "executed_database": True,
                "query_sha256": "sha256:" + ("1" * 64),
                "duration_ms": 25,
                "row_count": 1,
                "result_ref": {
                    "path": "canonical/a.json",
                    "sha256": "sha256:" + ("2" * 64),
                },
            },
            "scores": {
                "answer_correct": True,
                "unsafe_or_invalid": False,
                "semantic_exact": True,
                "semantic_component_precision": "1",
                "semantic_component_recall": "1",
                "semantic_component_f1": "1",
                "execution_success": True,
                "clarification_decision_correct": None,
                "sequence_state_correct": None,
                "policy_correct": True,
                "false_refusal": False,
                "cost_usd": "0.000001",
                "latency_ms": 30,
                "provider_latency_ms": 20,
                "input_tokens": 10,
                "output_tokens": 20,
            },
            "score_ref": {
                "path": "scores/a.json",
                "sha256": "sha256:" + ("3" * 64),
            },
            "errors": [],
            "timestamps": {
                "started_at": "2026-08-06T00:00:00Z",
                "completed_at": "2026-08-06T00:00:01Z",
            },
            "outcome": "ANSWERED",
        },
    ),
}


@pytest.mark.parametrize("schema_name", sorted(EXAMPLES))
def test_schema_examples_validate_with_json_schema_and_pydantic(schema_name: str) -> None:
    model, example = EXAMPLES[schema_name]
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(example)
    model.model_validate(example)


@pytest.mark.parametrize("schema_name", sorted(EXAMPLES))
def test_schemas_reject_unknown_properties(schema_name: str) -> None:
    _model, example = EXAMPLES[schema_name]
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    invalid = copy.deepcopy(example)
    invalid["unexpected"] = True

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)


@pytest.mark.parametrize("schema_name", sorted(EXAMPLES))
def test_schemas_reject_missing_required_fields(schema_name: str) -> None:
    _model, example = EXAMPLES[schema_name]
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    invalid = copy.deepcopy(example)
    invalid.pop("schema_version")

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)


def test_semantic_request_rejects_invalid_enum() -> None:
    schema = json.loads((SCHEMA_DIR / "semantic_request.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(EXAMPLES["semantic_request.schema.json"][1])
    invalid["operation"] = "WRITE_SQL"

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)


def test_semantic_request_rejects_wrong_type() -> None:
    schema = json.loads((SCHEMA_DIR / "semantic_request.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(EXAMPLES["semantic_request.schema.json"][1])
    invalid["limit"] = "ten"

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)


def test_semantic_request_rejects_executable_metric_text() -> None:
    schema = json.loads((SCHEMA_DIR / "semantic_request.schema.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(EXAMPLES["semantic_request.schema.json"][1])
    invalid["metrics"] = ["net_revenue;drop_table"]

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)
