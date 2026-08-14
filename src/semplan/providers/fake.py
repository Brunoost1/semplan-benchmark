"""Offline fake and replay providers for free tests and E2E validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from semplan.contracts import (
    CostEstimate,
    ProviderFinishStatus,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from semplan.errors import ErrorCode, ProjectError


class FakeProvider:
    """Return deterministic parsed payloads keyed by case ID or request hash."""

    def __init__(
        self,
        payloads: Mapping[str, dict[str, object]],
        *,
        provider: str = "fake",
        model: str = "fake-semantic-request-v1",
    ) -> None:
        self._payloads = dict(payloads)
        self.provider = provider
        self.model = model

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        key = request.metadata.get("case_id", request.idempotency_hash)
        payload = self._payloads.get(key)
        if payload is None:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Fake provider has no payload for request",
                detail={"key": key},
            )
        response_id = f"fake-{request.idempotency_hash.removeprefix('sha256:')[:16]}"
        raw_payload: dict[str, object] = {
            "id": response_id,
            "provider": self.provider,
            "model": self.model,
            "parsed": payload,
        }
        return ProviderResponse(
            schema_version="1.0",
            provider=self.provider,
            model=self.model,
            response_id=response_id,
            finish_status=ProviderFinishStatus.STOP,
            raw_payload=raw_payload,
            parsed_payload=payload,
            usage=_usage_for_request(request, payload),
            cost=CostEstimate(estimated_usd=Decimal("0")),
            timing_ms=0,
            attempts=1,
        )

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        _ = request
        return CostEstimate(estimated_usd=Decimal("0"))

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.OK, provider=self.provider)


class ReplayProvider:
    """Replay immutable provider responses captured in local JSONL fixtures."""

    def __init__(self, fixture_path: Path) -> None:
        self._responses: dict[str, ProviderResponse] = {}
        for line in fixture_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            record = json.loads(line)
            request_hash = record["request_idempotency_hash"]
            self._responses[request_hash] = ProviderResponse.model_validate(record["response"])

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        try:
            return self._responses[request.idempotency_hash]
        except KeyError as exc:
            raise ProjectError(
                ErrorCode.OUTPUT_SCHEMA_INVALID,
                "Replay fixture is missing the requested response",
                detail={"idempotency_hash": request.idempotency_hash},
            ) from exc

    def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        response = self._responses.get(request.idempotency_hash)
        if response is None:
            return CostEstimate(estimated_usd=Decimal("0"))
        return response.cost

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(status=ProviderHealthStatus.OK, provider="replay")


def _usage_for_request(
    request: ProviderRequest, parsed_payload: Mapping[str, object]
) -> ProviderUsage:
    input_tokens = sum(max(1, len(input_text.split())) for input_text in request.inputs)
    output_tokens = max(1, len(json.dumps(parsed_payload, sort_keys=True).split()))
    return ProviderUsage(input_tokens=input_tokens, output_tokens=output_tokens)
