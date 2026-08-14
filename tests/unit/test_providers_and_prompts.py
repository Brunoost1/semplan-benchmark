from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from semplan.contracts import CostEstimate, ProviderFinishStatus
from semplan.errors import ProjectError
from semplan.prompts import PromptRegistry
from semplan.providers import (
    FakeProvider,
    ReplayProvider,
    build_provider_request,
    provider_request_hash,
)


def test_provider_request_hash_changes_when_prompt_changes() -> None:
    base = {
        "schema_version": "1.0",
        "provider": "fake",
        "model": "fake",
        "prompt_id": "p1",
        "prompt_sha256": "sha256:" + ("a" * 64),
        "system": "one",
        "inputs": ["hello"],
        "output_schema_ref": "semantic_request.schema.json",
        "inference_parameters": {"temperature": "0"},
        "timeout_seconds": 30,
        "metadata": {},
    }
    changed = {**base, "system": "two"}

    assert provider_request_hash(base) != provider_request_hash(changed)


def test_fake_provider_returns_strict_response() -> None:
    request = build_provider_request(
        provider="fake",
        model="fake",
        prompt_id="p1",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system",
        inputs=["utterance"],
        output_schema_ref="semantic_request.schema.json",
        metadata={"case_id": "case-1"},
    )
    provider = FakeProvider(
        {
            "case-1": {
                "schema_version": "1.0",
                "operation": "OUT_OF_SCOPE",
                "intent": "out_of_scope",
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "time_grain": None,
                "sort": [],
                "limit": None,
                "comparison": None,
                "clarifications": [],
                "out_of_scope_reason": "WRITE_OPERATION",
                "confidence": "1",
            }
        }
    )

    response = provider.complete(request)

    assert response.finish_status is ProviderFinishStatus.STOP
    assert response.cost.estimated_usd == Decimal("0")
    assert response.parsed_payload is not None


def test_fake_provider_missing_payload_is_typed_error() -> None:
    request = build_provider_request(
        provider="fake",
        model="fake",
        prompt_id="p1",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system",
        inputs=["utterance"],
        output_schema_ref="semantic_request.schema.json",
        metadata={"case_id": "missing"},
    )

    with pytest.raises(ProjectError):
        FakeProvider({}).complete(request)


def test_replay_provider_replays_by_idempotency_hash(tmp_path: Path) -> None:
    request = build_provider_request(
        provider="fake",
        model="fake",
        prompt_id="p1",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system",
        inputs=["utterance"],
        output_schema_ref="semantic_request.schema.json",
    )
    response = FakeProvider(
        {
            request.idempotency_hash: {
                "schema_version": "1.0",
                "operation": "OUT_OF_SCOPE",
                "intent": "out_of_scope",
                "metrics": [],
                "dimensions": [],
                "filters": [],
                "time_grain": None,
                "sort": [],
                "limit": None,
                "comparison": None,
                "clarifications": [],
                "out_of_scope_reason": "WRITE_OPERATION",
                "confidence": "1",
            }
        }
    ).complete(request)
    fixture = tmp_path / "replay.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "request_idempotency_hash": request.idempotency_hash,
                "response": response.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    replayed = ReplayProvider(fixture).complete(request)

    assert replayed.response_id == response.response_id
    assert ReplayProvider(fixture).estimate_cost(request) == CostEstimate(
        estimated_usd=Decimal("0")
    )


def test_replay_provider_missing_response_is_typed_error(tmp_path: Path) -> None:
    fixture = tmp_path / "empty.jsonl"
    fixture.write_text("", encoding="utf-8")
    request = build_provider_request(
        provider="fake",
        model="fake",
        prompt_id="p1",
        prompt_sha256="sha256:" + ("a" * 64),
        system="system",
        inputs=["utterance"],
        output_schema_ref="semantic_request.schema.json",
    )

    with pytest.raises(ProjectError):
        ReplayProvider(fixture).complete(request)


def test_provider_healthchecks_are_local() -> None:
    assert FakeProvider({}).healthcheck().status == "OK"


def test_prompt_registry_loads_hashes_and_renders() -> None:
    registry = PromptRegistry.load(Path("prompts"))
    prompt = registry.get("semantic_request_a3_v1")

    rendered = prompt.render(
        {
            "locale": "en-US",
            "reference_date": "2026-08-01",
            "catalog_summary": "metrics: net_revenue",
            "utterance": "Show revenue.",
        }
    )

    assert prompt.sha256.startswith("sha256:")
    assert "Show revenue." in rendered


def test_prompt_registry_requires_variables() -> None:
    registry = PromptRegistry.load(Path("prompts"))

    with pytest.raises(ProjectError):
        registry.get("semantic_request_a4_v1").render({"locale": "en-US"})
