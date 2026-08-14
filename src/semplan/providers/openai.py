"""OpenAI Responses API provider adapter.

The adapter is deliberately fail-closed: construction is free, but live dispatch
requires an explicit paid flag and either an injected test client or
OPENAI_API_KEY for the official SDK client.
"""

from __future__ import annotations

import importlib
import json
import os
import time
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from semplan.contracts import (
    CostEstimate,
    PriceTable,
    ProviderFinishStatus,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from semplan.costs import actual_response_cost, estimate_request_cost
from semplan.errors import ErrorCode, ProjectError


class OpenAIProvider:
    """Provider-neutral wrapper around the official OpenAI Responses API."""

    provider = "openai"

    def __init__(
        self,
        *,
        price_table: PriceTable,
        allow_paid: bool = False,
        api_key: str | None = None,
        client: Any | None = None,
        schema_root: Path = Path("schemas"),
    ) -> None:
        self.price_table = price_table
        self.allow_paid = allow_paid
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = client
        self.schema_root = schema_root

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self.allow_paid:
            raise ProjectError(ErrorCode.BUDGET_EXCEEDED, "OpenAI calls require --allow-paid")
        client = self._client or self._build_client()
        payload = self.build_responses_payload(request)
        started = time.perf_counter()
        try:
            response = client.responses.create(**payload)
        except TimeoutError as exc:
            raise ProjectError(
                ErrorCode.PROVIDER_TIMEOUT,
                "OpenAI request timed out",
                retryable=True,
                detail=_provider_exception_detail(exc),
            ) from exc
        except Exception as exc:
            raise ProjectError(
                ErrorCode.PROVIDER_RATE_LIMIT,
                "OpenAI request failed before a typed response was returned",
                retryable=True,
                detail=_provider_exception_detail(exc),
            ) from exc
        timing_ms = int((time.perf_counter() - started) * 1000)
        return self._to_provider_response(request, response, timing_ms)

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        return estimate_request_cost(request, self.price_table)

    def healthcheck(self) -> ProviderHealth:
        if self._client is None and not self.api_key:
            return ProviderHealth(
                status=ProviderHealthStatus.UNAVAILABLE,
                provider=self.provider,
                detail="OPENAI_API_KEY is not set",
            )
        return ProviderHealth(status=ProviderHealthStatus.OK, provider=self.provider)

    def build_responses_payload(self, request: ProviderRequest) -> dict[str, object]:
        schema = self._schema_for_request(request)
        inference_parameters = dict(request.inference_parameters)
        reasoning_effort = inference_parameters.pop("reasoning_effort", None)
        payload: dict[str, object] = {
            "model": request.model,
            "instructions": request.system,
            "input": [{"role": "user", "content": input_text} for input_text in request.inputs],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.output_schema_ref.removesuffix(".schema.json"),
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        if isinstance(reasoning_effort, str) and reasoning_effort.lower() != "none":
            payload["reasoning"] = {"effort": reasoning_effort}
        payload.update(
            {key: value for key, value in inference_parameters.items() if value is not None}
        )
        return payload

    def _build_client(self) -> Any:
        if not self.api_key:
            raise ProjectError(ErrorCode.CFG_INVALID, "OPENAI_API_KEY is required")
        openai_module = importlib.import_module("openai")
        return openai_module.OpenAI(api_key=self.api_key)

    def _schema_for_request(self, request: ProviderRequest) -> dict[str, object]:
        schema_path = self.schema_root / request.output_schema_ref
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Output schema cannot be loaded for OpenAI request",
                detail={"schema": request.output_schema_ref, "reason": str(exc)},
            ) from exc
        if not isinstance(schema, dict):
            raise ProjectError(ErrorCode.CFG_INVALID, "Output schema must be a JSON object")
        return _openai_strict_schema(schema)

    def _to_provider_response(
        self,
        request: ProviderRequest,
        response: Any,
        timing_ms: int,
    ) -> ProviderResponse:
        raw_payload = _raw_payload(response)
        finish_status = _finish_status(raw_payload)
        refusal = _refusal_text(raw_payload)
        try:
            parsed_payload = _parsed_payload(raw_payload)
        except ProjectError as exc:
            if exc.record.code is not ErrorCode.OUTPUT_SCHEMA_INVALID:
                raise
            raw_payload = dict(raw_payload)
            raw_payload["_semplan_parse_error"] = exc.record.model_dump(mode="json")
            parsed_payload = None
            finish_status = ProviderFinishStatus.ERROR
        usage = _usage(raw_payload)
        provider_response = ProviderResponse(
            schema_version="1.0",
            provider=self.provider,
            model=str(raw_payload.get("model") or request.model),
            response_id=str(raw_payload.get("id") or f"openai-{request.idempotency_hash[-16:]}"),
            finish_status=finish_status,
            raw_payload=raw_payload,
            parsed_payload=parsed_payload,
            usage=usage,
            cost=CostEstimate(estimated_usd=Decimal("0")),
            timing_ms=timing_ms,
            attempts=1,
            refusal=refusal,
        )
        actual = actual_response_cost(provider_response, self.price_table)
        return provider_response.model_copy(update={"cost": actual})


def _raw_payload(response: Any) -> dict[str, object]:
    if hasattr(response, "model_dump"):
        raw = response.model_dump(mode="json")
    elif isinstance(response, dict):
        raw = response
    else:
        raw = vars(response)
    if not isinstance(raw, dict):
        raise ProjectError(ErrorCode.OUTPUT_SCHEMA_INVALID, "OpenAI response is not an object")
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        raw.setdefault("output_text", output_text)
    return raw


def _provider_exception_detail(exc: Exception) -> dict[str, object]:
    """Return redacted provider exception metadata for operational retry triage."""

    detail: dict[str, object] = {"error_type": type(exc).__name__}
    for attr, key in (
        ("status_code", "status_code"),
        ("request_id", "request_id"),
        ("code", "provider_error_code"),
        ("type", "provider_error_type"),
    ):
        value = getattr(exc, attr, None)
        if isinstance(value, str | int):
            detail[key] = value
    return detail


def _finish_status(raw_payload: dict[str, object]) -> ProviderFinishStatus:
    if _refusal_text(raw_payload) is not None:
        return ProviderFinishStatus.REFUSAL
    status = str(raw_payload.get("status") or "completed").lower()
    if status in {"completed", "succeeded"}:
        return ProviderFinishStatus.STOP
    if status == "incomplete":
        return ProviderFinishStatus.INCOMPLETE
    if status in {"failed", "error", "cancelled"}:
        return ProviderFinishStatus.ERROR
    return ProviderFinishStatus.INCOMPLETE


def _parsed_payload(raw_payload: dict[str, object]) -> dict[str, object] | None:
    text = raw_payload.get("output_text")
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "OpenAI structured output is not valid JSON",
                detail={"reason": str(exc)},
            ) from exc
        if not isinstance(parsed, dict):
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID, "OpenAI output JSON is not an object"
            )
        return parsed
    parsed = raw_payload.get("parsed")
    if isinstance(parsed, dict):
        return parsed
    return None


def _refusal_text(raw_payload: dict[str, object]) -> str | None:
    refusal = raw_payload.get("refusal")
    if isinstance(refusal, str) and refusal:
        return refusal
    output = raw_payload.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                item_refusal = item.get("refusal")
                if isinstance(item_refusal, str):
                    return item_refusal
    return None


def _usage(raw_payload: dict[str, object]) -> ProviderUsage:
    usage = raw_payload.get("usage")
    if not isinstance(usage, dict):
        return ProviderUsage(input_tokens=0, output_tokens=0)
    input_tokens = _int_field(usage, "input_tokens", "prompt_tokens")
    output_tokens = _int_field(usage, "output_tokens", "completion_tokens")
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    cached_tokens = (
        _int_field(input_details, "cached_tokens") if isinstance(input_details, dict) else 0
    )
    reasoning_tokens = (
        _int_field(output_details, "reasoning_tokens") if isinstance(output_details, dict) else 0
    )
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def _int_field(payload: dict[str, object], *names: str) -> int:
    for name in names:
        value = payload.get(name)
        if isinstance(value, int):
            return value
    return 0


def _openai_strict_schema(schema: dict[str, object]) -> dict[str, object]:
    """Convert exported Pydantic JSON Schema into OpenAI strict-schema shape."""

    sanitized = deepcopy(schema)
    _sanitize_schema_node(sanitized)
    return sanitized


def _sanitize_schema_node(node: object) -> None:
    if isinstance(node, dict):
        node.pop("$id", None)
        node.pop("$schema", None)
        node.pop("default", None)
        node.pop("discriminator", None)
        node.pop("pattern", None)
        if "oneOf" in node and "anyOf" not in node:
            node["anyOf"] = node.pop("oneOf")
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["additionalProperties"] = False
            node["required"] = list(properties)
        for value in node.values():
            _sanitize_schema_node(value)
    elif isinstance(node, list):
        for item in node:
            _sanitize_schema_node(item)
